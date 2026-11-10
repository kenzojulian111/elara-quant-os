    const csrfToken = (document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '').trim();
    let currentProfile = null;

    const errorMsg = document.getElementById('error-msg');
    const infoMsg = document.getElementById('info-msg');

    function setError(message = '') {
        errorMsg.textContent = message || '';
        errorMsg.style.display = message ? 'block' : 'none';
    }

    function setInfo(message = '') {
        infoMsg.textContent = message || '';
        infoMsg.style.display = message ? 'block' : 'none';
    }

    function clearNotice() {
        setError('');
        setInfo('');
    }

    async function fetchJson(url, opts = {}) {
        const headers = {
            ...(opts.headers || {}),
            ...(csrfToken ? { 'X-CSRFToken': csrfToken, 'X-CSRF-Token': csrfToken } : {}),
        };
        const res = await fetch(url, { ...opts, headers });
        if (res.status === 401) {
            window.location.href = '/login';
            return null;
        }
        const data = await res.json().catch(() => ({}));
        return { ok: res.ok, status: res.status, data };
    }

    function normalizeOtp(value) {
        return String(value || '').replace(/\D/g, '').slice(0, 6);
    }

    function renderProfile(profile) {
        currentProfile = profile || {};
        const email = currentProfile.email || '-';
        const name = currentProfile.name || '-';
        const role = String(currentProfile.role || 'user').toUpperCase();
        const security = currentProfile.security || {};

        document.getElementById('whoami').innerText = `${name} (${email})`;
        document.getElementById('meta-email').innerText = email;
        document.getElementById('meta-role').innerText = role;
        document.getElementById('meta-verified').innerText = security.email_verified ? 'YES' : 'NO';
        document.getElementById('meta-mfa').innerText = security.mfa_enabled ? 'YES' : 'NO';
        document.getElementById('profile-name').value = currentProfile.name || '';
        document.getElementById('verify-email').value = currentProfile.email || '';

        const isAdmin = ['admin', 'superuser'].includes(String(currentProfile.role || '').toLowerCase());
        document.getElementById('admin-mfa-card').hidden = !isAdmin;
    }

    function renderMfaStatus(data) {
        if (!data) return;
        document.getElementById('meta-mfa').innerText = data.mfa_enabled ? 'YES' : 'NO';
        const note = data.mfa_enabled
            ? 'MFA aktif. Gunakan OTP valid untuk disable jika diperlukan.'
            : 'MFA belum aktif. Generate secret lalu enable.';
        document.getElementById('mfa-note').innerText = note;
    }

    async function loadProfile() {
        const out = await fetchJson('/api/account/profile');
        if (!out) return;
        if (!out.ok) {
            setError((out.data && out.data.message) || 'Gagal load profile.');
            return;
        }
        renderProfile(out.data || {});
    }

    async function refreshMfaStatus() {
        const out = await fetchJson('/api/account/mfa/status');
        if (!out || !out.ok) return;
        renderMfaStatus(out.data || {});
    }

    document.getElementById('profile-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        clearNotice();
        const name = document.getElementById('profile-name').value.trim();
        if (!name) {
            setError('Display name wajib diisi.');
            return;
        }
        const out = await fetchJson('/api/account/profile', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        if (!out) return;
        if (!out.ok) {
            setError((out.data && out.data.message) || 'Update profile gagal.');
            return;
        }
        setInfo('Profile berhasil diperbarui.');
        await loadProfile();
    });

    document.getElementById('password-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        clearNotice();
        const currentPassword = document.getElementById('current-password').value;
        const newPassword = document.getElementById('new-password').value;
        const confirmPassword = document.getElementById('new-password-confirm').value;

        if (!currentPassword || !newPassword) {
            setError('Field password wajib diisi.');
            return;
        }
        if (newPassword.length < 8) {
            setError('Password baru minimal 8 karakter.');
            return;
        }
        if (newPassword !== confirmPassword) {
            setError('Konfirmasi password tidak sama.');
            return;
        }

        const out = await fetchJson('/api/account/password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
        });
        if (!out) return;
        if (!out.ok) {
            setError((out.data && out.data.message) || 'Gagal update password.');
            return;
        }
        setInfo((out.data && out.data.message) || 'Password diperbarui.');
        document.getElementById('current-password').value = '';
        document.getElementById('new-password').value = '';
        document.getElementById('new-password-confirm').value = '';
    });

    document.getElementById('verify-request-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        clearNotice();
        const email = document.getElementById('verify-email').value.trim();
        const out = await fetchJson('/api/email/verify/request', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email }),
        });
        if (!out) return;
        if (!out.ok) {
            setError((out.data && out.data.message) || 'Request verify token gagal.');
            return;
        }
        if (out.data && out.data.verification_token) {
            document.getElementById('verify-token').value = out.data.verification_token;
        }
        setInfo((out.data && out.data.message) || 'Request verifikasi berhasil.');
    });

    document.getElementById('verify-confirm-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        clearNotice();
        const token = document.getElementById('verify-token').value.trim();
        if (!token) {
            setError('Token verifikasi wajib diisi.');
            return;
        }
        const out = await fetchJson('/api/email/verify/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });
        if (!out) return;
        if (!out.ok) {
            setError((out.data && out.data.message) || 'Verifikasi gagal.');
            return;
        }
        setInfo((out.data && out.data.message) || 'Email berhasil diverifikasi.');
        document.getElementById('verify-token').value = '';
        await loadProfile();
        await refreshMfaStatus();
    });

    document.getElementById('mfa-setup-btn').addEventListener('click', async () => {
        clearNotice();
        const out = await fetchJson('/api/account/mfa/setup', { method: 'POST' });
        if (!out) return;
        if (!out.ok) {
            setError((out.data && out.data.message) || 'Gagal setup MFA.');
            return;
        }
        const card = document.getElementById('mfa-secret');
        card.hidden = false;
        document.getElementById('mfa-secret-text').innerText = out.data.secret || '-';
        document.getElementById('mfa-uri-text').innerText = out.data.otpauth_uri || '-';
        setInfo('Secret MFA baru diterbitkan. Input ke authenticator lalu enable dengan OTP.');
        await refreshMfaStatus();
    });

    document.getElementById('mfa-enable-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        clearNotice();
        const otp = normalizeOtp(document.getElementById('mfa-enable-otp').value);
        if (otp.length !== 6) {
            setError('OTP harus 6 digit.');
            return;
        }
        const out = await fetchJson('/api/account/mfa/enable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ otp }),
        });
        if (!out) return;
        if (!out.ok) {
            setError((out.data && out.data.message) || 'Enable MFA gagal.');
            return;
        }
        setInfo('MFA admin berhasil diaktifkan.');
        document.getElementById('mfa-enable-otp').value = '';
        await loadProfile();
        await refreshMfaStatus();
    });

    document.getElementById('mfa-disable-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        clearNotice();
        const otp = normalizeOtp(document.getElementById('mfa-disable-otp').value);
        if (otp.length !== 6) {
            setError('OTP harus 6 digit.');
            return;
        }
        const out = await fetchJson('/api/account/mfa/disable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ otp }),
        });
        if (!out) return;
        if (!out.ok) {
            setError((out.data && out.data.message) || 'Disable MFA gagal.');
            return;
        }
        setInfo('MFA admin berhasil dimatikan.');
        document.getElementById('mfa-disable-otp').value = '';
        await loadProfile();
        await refreshMfaStatus();
    });

    document.getElementById('mfa-refresh-btn').addEventListener('click', async () => {
        clearNotice();
        await refreshMfaStatus();
        setInfo('MFA status diperbarui.');
    });

    document.getElementById('open-portal').addEventListener('click', () => {
        window.location.href = '/portal';
    });

    document.getElementById('logout-btn').addEventListener('click', async () => {
        try { await fetchJson('/api/logout', { method: 'POST' }); } catch (_) {}
        window.location.href = '/login';
    });

    (async () => {
        await loadProfile();
        await refreshMfaStatus();
    })();
