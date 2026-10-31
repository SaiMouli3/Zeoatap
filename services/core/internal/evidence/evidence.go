// Package evidence encrypts incident clips at rest. Each clip gets its own
// random AES-256-GCM key; that key is stored only wrapped by the master key.
// Destroying the wrapped key (crypto-shredding) makes the clip unreadable even
// if a copy of the file survives.
package evidence

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// MaxClipBytes bounds a clip held in memory for encryption (≈2 min at 4 Mbps ×
// several). Larger inputs are refused rather than truncated.
const MaxClipBytes = 512 << 20

type Vault struct {
	Dir    string
	master cipher.AEAD
}

// NewVault takes a 32-byte master key (from the secret store, never the DB).
func NewVault(dir string, masterKey []byte) (*Vault, error) {
	if len(masterKey) != 32 {
		return nil, errors.New("master key must be 32 bytes")
	}
	aead, err := newGCM(masterKey)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	return &Vault{Dir: dir, master: aead}, nil
}

func newGCM(key []byte) (cipher.AEAD, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

func seal(aead cipher.AEAD, plain, aad []byte) ([]byte, error) {
	nonce := make([]byte, aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return aead.Seal(nonce, nonce, plain, aad), nil
}

func open(aead cipher.AEAD, sealed, aad []byte) ([]byte, error) {
	n := aead.NonceSize()
	if len(sealed) < n {
		return nil, errors.New("ciphertext too short")
	}
	return aead.Open(nil, sealed[:n], sealed[n:], aad)
}

type Stored struct {
	Path       string
	SHA256     string // of the plaintext
	Size       int64
	WrappedKey []byte
}

// Store encrypts the plaintext file at src into the vault under clipID and
// removes src. The clip ID is bound into the ciphertext so files cannot be
// swapped between records.
func (v *Vault) Store(clipID, src string) (*Stored, error) {
	f, err := os.Open(src)
	if err != nil {
		return nil, err
	}
	plain, err := io.ReadAll(io.LimitReader(f, MaxClipBytes+1))
	f.Close()
	if err != nil {
		return nil, err
	}
	if len(plain) > MaxClipBytes {
		return nil, fmt.Errorf("clip larger than %d bytes", MaxClipBytes)
	}
	sum := sha256.Sum256(plain)

	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return nil, err
	}
	clipAEAD, err := newGCM(key)
	if err != nil {
		return nil, err
	}
	ct, err := seal(clipAEAD, plain, []byte(clipID))
	if err != nil {
		return nil, err
	}
	wrapped, err := seal(v.master, key, []byte("clip-key:"+clipID))
	if err != nil {
		return nil, err
	}

	dst := filepath.Join(v.Dir, clipID+".enc")
	tmp := dst + ".part"
	if err := os.WriteFile(tmp, ct, 0o600); err != nil {
		return nil, err
	}
	if err := os.Rename(tmp, dst); err != nil {
		os.Remove(tmp)
		return nil, err
	}
	if err := os.Remove(src); err != nil {
		return nil, fmt.Errorf("encrypted, but could not remove plaintext %s: %w", src, err)
	}
	return &Stored{Path: dst, SHA256: hex.EncodeToString(sum[:]), Size: int64(len(plain)), WrappedKey: wrapped}, nil
}

// Open decrypts a clip and verifies it against its recorded hash.
func (v *Vault) Open(clipID, path string, wrappedKey []byte, wantSHA string) ([]byte, error) {
	if wrappedKey == nil {
		return nil, errors.New("clip has been deleted (key destroyed)")
	}
	key, err := open(v.master, wrappedKey, []byte("clip-key:"+clipID))
	if err != nil {
		return nil, fmt.Errorf("unwrap key: %w", err)
	}
	clipAEAD, err := newGCM(key)
	if err != nil {
		return nil, err
	}
	ct, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	plain, err := open(clipAEAD, ct, []byte(clipID))
	if err != nil {
		return nil, fmt.Errorf("decrypt: %w", err)
	}
	sum := sha256.Sum256(plain)
	if hex.EncodeToString(sum[:]) != wantSHA {
		return nil, errors.New("clip does not match its recorded hash")
	}
	return plain, nil
}

// Remove deletes the ciphertext file. Call after the key is destroyed.
func (v *Vault) Remove(path string) error {
	err := os.Remove(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}

// CheckWritable writes and removes a probe file, for health reporting.
func (v *Vault) CheckWritable() error {
	f, err := os.CreateTemp(v.Dir, ".probe-*")
	if err != nil {
		return err
	}
	name := f.Name()
	_, werr := f.Write([]byte("ok"))
	cerr := f.Close()
	rerr := os.Remove(name)
	return errors.Join(werr, cerr, rerr)
}
