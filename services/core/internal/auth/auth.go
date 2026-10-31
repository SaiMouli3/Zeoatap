// Package auth handles local accounts, cookie sessions and role checks.
package auth

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"net/http"
	"time"

	"golang.org/x/crypto/bcrypt"

	"github.com/saimouli3/zeoatap/services/core/internal/store"
)

const (
	CookieName = "zeoatap_session"
	// Shared wall tablets: an idle session ends after 15 minutes (PRIVACY §4).
	SessionTTL = 15 * time.Minute
)

// Role permissions, from the RBAC table in PRIVACY_SECURITY_COMPLIANCE §4.
type Permission string

const (
	PermViewStatus   Permission = "view_status"
	PermAcknowledge  Permission = "acknowledge"
	PermViewClips    Permission = "view_clips"
	PermDisposition  Permission = "disposition"
	PermViewShadow   Permission = "view_shadow"
	PermAdmin        Permission = "admin"
	PermLegalHold    Permission = "legal_hold"
)

var rolePerms = map[string]map[Permission]bool{
	"caregiver": {PermViewStatus: true, PermAcknowledge: true},
	"lead":      {PermViewStatus: true, PermAcknowledge: true, PermViewClips: true},
	"reviewer":  {PermViewStatus: true, PermAcknowledge: true, PermViewClips: true, PermDisposition: true, PermViewShadow: true, PermLegalHold: true},
	"admin":     {PermViewStatus: true, PermAcknowledge: true, PermAdmin: true, PermViewShadow: true, PermLegalHold: true},
}

func Can(role string, p Permission) bool { return rolePerms[role][p] }

func ValidRole(role string) bool { _, ok := rolePerms[role]; return ok }

func HashPassword(pw string) (string, error) {
	if len(pw) < 10 {
		return "", errors.New("password must be at least 10 characters")
	}
	b, err := bcrypt.GenerateFromPassword([]byte(pw), 12)
	return string(b), err
}

func CheckPassword(hash, pw string) bool {
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(pw)) == nil
}

// dummyHash keeps login timing the same whether or not the user exists.
var dummyHash, _ = bcrypt.GenerateFromPassword([]byte("not-a-real-password"), 12)

type Service struct {
	DB           *store.DB
	SecureCookie bool
}

func tokenHash(tok string) string {
	sum := sha256.Sum256([]byte(tok))
	return hex.EncodeToString(sum[:])
}

// ErrInvalidLogin is returned for an unknown user, a wrong password or a
// disabled account, without saying which.
var ErrInvalidLogin = errors.New("invalid username or password")

func (s *Service) Login(ctx context.Context, w http.ResponseWriter, username, password string) (*store.User, error) {
	u, err := s.DB.UserByName(ctx, username)
	if errors.Is(err, store.ErrNotFound) {
		_ = bcrypt.CompareHashAndPassword(dummyHash, []byte(password))
		return nil, ErrInvalidLogin
	}
	if err != nil {
		return nil, err
	}
	if !CheckPassword(u.PasswordHash, password) || u.Disabled {
		return nil, ErrInvalidLogin
	}
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return nil, err
	}
	tok := base64.RawURLEncoding.EncodeToString(raw)
	if err := s.DB.CreateSession(ctx, tokenHash(tok), u.ID, SessionTTL); err != nil {
		return nil, err
	}
	http.SetCookie(w, &http.Cookie{Name: CookieName, Value: tok, Path: "/", HttpOnly: true,
		Secure: s.SecureCookie, SameSite: http.SameSiteStrictMode})
	return u, nil
}

func (s *Service) Logout(ctx context.Context, w http.ResponseWriter, r *http.Request) {
	if c, err := r.Cookie(CookieName); err == nil {
		_ = s.DB.DeleteSession(ctx, tokenHash(c.Value))
	}
	http.SetCookie(w, &http.Cookie{Name: CookieName, Value: "", Path: "/", MaxAge: -1, HttpOnly: true,
		Secure: s.SecureCookie, SameSite: http.SameSiteStrictMode})
}

// UserFrom returns the signed-in user and slides the session's idle timeout.
// It returns store.ErrNotFound when there is no live session.
func (s *Service) UserFrom(r *http.Request) (*store.User, error) {
	c, err := r.Cookie(CookieName)
	if err != nil || c.Value == "" {
		return nil, store.ErrNotFound
	}
	return s.DB.SessionUser(r.Context(), tokenHash(c.Value), SessionTTL)
}

// StillValid reports whether the request's session is live, without counting
// as activity. Long-lived streams use it so they cannot keep a shared tablet
// signed in forever.
func (s *Service) StillValid(ctx context.Context, r *http.Request) (bool, error) {
	c, err := r.Cookie(CookieName)
	if err != nil || c.Value == "" {
		return false, nil
	}
	_, err = s.DB.SessionUserNoTouch(ctx, tokenHash(c.Value))
	if errors.Is(err, store.ErrNotFound) {
		return false, nil
	}
	return err == nil, err
}

// Permissions lists a role's permissions in a stable order.
func Permissions(role string) []Permission {
	out := []Permission{}
	for _, p := range []Permission{PermViewStatus, PermAcknowledge, PermViewClips, PermDisposition, PermViewShadow, PermLegalHold, PermAdmin} {
		if Can(role, p) {
			out = append(out, p)
		}
	}
	return out
}

type ctxKey struct{}

func WithUser(ctx context.Context, u *store.User) context.Context { return context.WithValue(ctx, ctxKey{}, u) }

func UserOf(ctx context.Context) *store.User {
	u, _ := ctx.Value(ctxKey{}).(*store.User)
	return u
}
