package evidence

import (
	"bytes"
	"crypto/rand"
	"os"
	"path/filepath"
	"testing"
)

func newVault(t *testing.T) *Vault {
	t.Helper()
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		t.Fatal(err)
	}
	v, err := NewVault(t.TempDir(), key)
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func writePlain(t *testing.T, data []byte) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "clip.mp4")
	if err := os.WriteFile(p, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestStoreOpenRoundTrip(t *testing.T) {
	v := newVault(t)
	plain := []byte("fake mp4 bytes \x00\x01\x02")
	src := writePlain(t, plain)
	st, err := v.Store("INC-1-cam1", src)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(src); !os.IsNotExist(err) {
		t.Fatal("plaintext must be removed after encryption")
	}
	ct, err := os.ReadFile(st.Path)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(ct, plain) {
		t.Fatal("ciphertext contains the plaintext")
	}
	got, err := v.Open("INC-1-cam1", st.Path, st.WrappedKey, st.SHA256)
	if err != nil || !bytes.Equal(got, plain) {
		t.Fatalf("open: %q %v", got, err)
	}
	if err := v.CheckWritable(); err != nil {
		t.Fatal(err)
	}
}

func TestOpenRejectsWrongIDTamperingAndShred(t *testing.T) {
	v := newVault(t)
	st, err := v.Store("INC-1-cam1", writePlain(t, []byte("evidence")))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := v.Open("INC-2-cam1", st.Path, st.WrappedKey, st.SHA256); err == nil {
		t.Fatal("a clip must not open under another clip's ID")
	}
	if _, err := v.Open("INC-1-cam1", st.Path, st.WrappedKey, "00"); err == nil {
		t.Fatal("hash mismatch must be rejected")
	}

	ct, _ := os.ReadFile(st.Path)
	ct[len(ct)-1] ^= 0xff
	tampered := filepath.Join(v.Dir, "tampered.enc")
	if err := os.WriteFile(tampered, ct, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := v.Open("INC-1-cam1", tampered, st.WrappedKey, st.SHA256); err == nil {
		t.Fatal("tampered ciphertext must be rejected")
	}

	other := newVault(t)
	if _, err := other.Open("INC-1-cam1", st.Path, st.WrappedKey, st.SHA256); err == nil {
		t.Fatal("a different master key must not unwrap the clip key")
	}

	// Crypto-shred: without the wrapped key the surviving file is unreadable.
	if _, err := v.Open("INC-1-cam1", st.Path, nil, st.SHA256); err == nil {
		t.Fatal("shredded clip must be unreadable")
	}
	if err := v.Remove(st.Path); err != nil {
		t.Fatal(err)
	}
	if err := v.Remove(st.Path); err != nil {
		t.Fatal("removing twice must be fine")
	}
}

func TestNewVaultRejectsShortKey(t *testing.T) {
	if _, err := NewVault(t.TempDir(), make([]byte, 16)); err == nil {
		t.Fatal("16-byte master key must be refused")
	}
}
