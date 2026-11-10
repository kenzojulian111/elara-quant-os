# Zero-Downtime Migration Pattern

Gunakan pola expand/migrate/contract untuk perubahan kolom yang dipakai service aktif:

1. **Expand**
- Tambah kolom/baris baru (nullable/default aman).
- Deploy aplikasi yang **write ke kolom lama + baru** (dual-write).

2. **Migrate**
- Jalankan backfill bertahap (batch) untuk data lama.
- Pantau error rate, query latency, dan jumlah row yang belum termigrasi.

3. **Flip Read Path**
- Ganti read path ke kolom baru lewat feature flag.
- Biarkan dual-write aktif selama periode observasi.

4. **Contract**
- Hentikan write ke kolom lama.
- Drop kolom lama setelah verifikasi final.

## Checklist Operasional
- Migration script idempotent.
- Batch backfill punya retry + checkpoint.
- Ada rollback plan untuk step read-path flip.
- Query critical sudah diuji di shadow/staging.

