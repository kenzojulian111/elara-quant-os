        const SIGNIN_MODE = 'signin';
        const SIGNUP_MODE = 'signup';
        const FORGOT_MODE = 'forgot';
        const RESET_MODE = 'reset';
        const VERIFY_MODE = 'verify';

        const initialModeRaw = String(window.__ELARA_AUTH_MODE__ || SIGNIN_MODE).toLowerCase();
        const initialMode = [SIGNIN_MODE, SIGNUP_MODE, FORGOT_MODE, RESET_MODE, VERIFY_MODE].includes(initialModeRaw)
            ? initialModeRaw
            : SIGNIN_MODE;

        const prefillToken = String(window.__ELARA_PREFILL_TOKEN__ || '').trim();
        const prefillEmail = String(window.__ELARA_PREFILL_EMAIL__ || '').trim();
        const csrfToken = (document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '').trim();

        const signinForm = document.getElementById('signin-form');
        const signupForm = document.getElementById('signup-form');
        const forgotForm = document.getElementById('forgot-form');
        const resetForm = document.getElementById('reset-form');
        const verifyRequestForm = document.getElementById('verify-request-form');
        const verifyConfirmForm = document.getElementById('verify-confirm-form');

        const signinEmailInput = document.getElementById('signin-email');
        const signinPasswordInput = document.getElementById('signin-password');
        const signinOtpInput = document.getElementById('signin-otp');
        const mfaGroup = document.getElementById('mfa-group');
        const signupNameInput = document.getElementById('signup-name');
        const signupEmailInput = document.getElementById('signup-email');
        const signupPasswordInput = document.getElementById('signup-password');
        const signupPasswordConfirmInput = document.getElementById('signup-password-confirm');
        const forgotEmailInput = document.getElementById('forgot-email');
        const resetTokenInput = document.getElementById('reset-token');
        const resetPasswordInput = document.getElementById('reset-password');
        const resetPasswordConfirmInput = document.getElementById('reset-password-confirm');
        const verifyEmailInput = document.getElementById('verify-email');
        const verifyTokenInput = document.getElementById('verify-token');

        const signinBtn = document.getElementById('signin-btn');
        const signupBtn = document.getElementById('signup-btn');
        const forgotBtn = document.getElementById('forgot-btn');
        const resetBtn = document.getElementById('reset-btn');
        const verifyRequestBtn = document.getElementById('verify-request-btn');
        const verifyConfirmBtn = document.getElementById('verify-confirm-btn');

        const errorMsg = document.getElementById('error-msg');
        const infoMsg = document.getElementById('info-msg');
        const authHelper = document.getElementById('auth-helper');

        const paneSignin = document.getElementById('pane-signin');
        const paneSignup = document.getElementById('pane-signup');
        const paneForgot = document.getElementById('pane-forgot');
        const paneReset = document.getElementById('pane-reset');
        const paneVerify = document.getElementById('pane-verify');
        const tabSignin = document.getElementById('tab-signin');
        const tabSignup = document.getElementById('tab-signup');

        function setError(message = '') {
            if (!message) {
                errorMsg.style.display = 'none';
                errorMsg.textContent = '';
                return;
            }
            errorMsg.textContent = message;
            errorMsg.style.display = 'block';
        }

        function setInfo(message = '') {
            if (!message) {
                infoMsg.hidden = true;
                infoMsg.textContent = '';
                return;
            }
            infoMsg.hidden = false;
            infoMsg.textContent = message;
        }

        function resetNotice() {
            setError('');
            setInfo('');
        }

        function authRequest(path, body) {
            return fetch(path, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(csrfToken ? { 'X-CSRFToken': csrfToken, 'X-CSRF-Token': csrfToken } : {}),
                },
                body: JSON.stringify(body || {}),
            });
        }

        function setMode(mode, updateUrl = true) {
            const safe = [SIGNIN_MODE, SIGNUP_MODE, FORGOT_MODE, RESET_MODE, VERIFY_MODE].includes(String(mode || '').toLowerCase())
                ? String(mode || '').toLowerCase()
                : SIGNIN_MODE;

            paneSignin.hidden = safe !== SIGNIN_MODE;
            paneSignup.hidden = safe !== SIGNUP_MODE;
            paneForgot.hidden = safe !== FORGOT_MODE;
            paneReset.hidden = safe !== RESET_MODE;
            paneVerify.hidden = safe !== VERIFY_MODE;

            tabSignin.classList.toggle('active', safe === SIGNIN_MODE);
            tabSignup.classList.toggle('active', safe === SIGNUP_MODE);

            resetNotice();

            if (authHelper) {
                if (safe === SIGNUP_MODE) {
                    authHelper.innerHTML = 'Buat akun baru. Endpoint: <code>/api/signup</code>';
                } else if (safe === FORGOT_MODE || safe === RESET_MODE) {
                    authHelper.innerHTML = 'Forgot/reset password via <code>/api/password/forgot</code> dan <code>/api/password/reset</code>.';
                } else if (safe === VERIFY_MODE) {
                    authHelper.innerHTML = 'Verifikasi email via <code>/api/email/verify/request</code> dan <code>/api/email/verify/confirm</code>.';
                } else {
                    authHelper.innerHTML = 'Session diproteksi via cookie HTTP-only. Endpoint: <code>/api/signin</code>.';
                }
            }

            if (safe !== SIGNIN_MODE) {
                mfaGroup.hidden = true;
                signinOtpInput.value = '';
            }

            if (safe === RESET_MODE && prefillToken && !resetTokenInput.value.trim()) {
                resetTokenInput.value = prefillToken;
            }
            if ((safe === VERIFY_MODE || safe === FORGOT_MODE) && prefillEmail && !verifyEmailInput.value.trim()) {
                verifyEmailInput.value = prefillEmail;
                forgotEmailInput.value = prefillEmail;
            }

            if (updateUrl) {
                const pathMap = {
                    [SIGNIN_MODE]: '/login',
                    [SIGNUP_MODE]: '/signup',
                    [FORGOT_MODE]: '/forgot-password',
                    [RESET_MODE]: '/reset-password',
                    [VERIFY_MODE]: '/verify-email',
                };
                const target = pathMap[safe] || '/login';
                if (window.location.pathname !== target) {
                    window.history.replaceState({}, '', target);
                }
            }
        }

        async function redirectIfLoggedIn() {
            if (initialMode !== SIGNIN_MODE && initialMode !== SIGNUP_MODE) return;
            try {
                const res = await fetch('/api/me', {
                    headers: {
                        ...(csrfToken ? { 'X-CSRFToken': csrfToken, 'X-CSRF-Token': csrfToken } : {}),
                    },
                });
                if (res.ok) {
                    window.location.href = '/portal';
                }
            } catch (_) {}
        }

        tabSignin.addEventListener('click', () => setMode(SIGNIN_MODE));
        tabSignup.addEventListener('click', () => setMode(SIGNUP_MODE));

        document.getElementById('open-forgot').addEventListener('click', () => setMode(FORGOT_MODE));
        document.getElementById('open-verify').addEventListener('click', () => setMode(VERIFY_MODE));
        document.getElementById('open-reset').addEventListener('click', () => setMode(RESET_MODE));
        document.getElementById('back-from-forgot').addEventListener('click', () => setMode(SIGNIN_MODE));
        document.getElementById('back-from-reset').addEventListener('click', () => setMode(SIGNIN_MODE));
        document.getElementById('back-from-verify').addEventListener('click', () => setMode(SIGNIN_MODE));

        signinForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            resetNotice();

            const email = signinEmailInput.value.trim();
            const password = signinPasswordInput.value;
            const otp = signinOtpInput.value.trim();
            if (!email || !password) {
                setError('Email dan password wajib diisi.');
                return;
            }

            signinBtn.disabled = true;
            signinBtn.textContent = 'Connecting...';
            try {
                const payload = { email, password };
                if (otp) payload.otp = otp;
                const res = await authRequest('/api/signin', payload);
                const data = await res.json().catch(() => ({}));

                if (data && data.mfa_required) {
                    mfaGroup.hidden = false;
                    setInfo(data.message || 'Masukkan OTP admin dari authenticator.');
                    return;
                }
                if (!res.ok) {
                    if (data && data.email_verification_required) {
                        verifyEmailInput.value = email;
                        setMode(VERIFY_MODE);
                        setInfo(data.message || 'Email belum diverifikasi. Silakan verify dulu.');
                    } else {
                        setError((data && data.message) || 'Access Denied');
                    }
                    return;
                }

                if (data && data.access_token) {
                    try { sessionStorage.setItem('elara_token_mem', data.access_token); } catch (_) {}
                }
                window.location.href = '/portal';
            } catch (_) {
                setError('Network error. Coba lagi.');
            } finally {
                signinBtn.disabled = false;
                signinBtn.textContent = 'Initialize Session';
            }
        });

        signupForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            resetNotice();

            const name = signupNameInput.value.trim();
            const email = signupEmailInput.value.trim();
            const password = signupPasswordInput.value;
            const passwordConfirm = signupPasswordConfirmInput.value;

            if (!email || !password) {
                setError('Email dan password wajib diisi.');
                return;
            }
            if (password.length < 8) {
                setError('Password minimal 8 karakter.');
                return;
            }
            if (password !== passwordConfirm) {
                setError('Konfirmasi password tidak sama.');
                return;
            }

            signupBtn.disabled = true;
            signupBtn.textContent = 'Creating...';
            try {
                const res = await authRequest('/api/signup', { name, email, password });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    setError((data && data.message) || 'Sign up gagal');
                    return;
                }
                if (data && data.access_token) {
                    try { sessionStorage.setItem('elara_token_mem', data.access_token); } catch (_) {}
                    window.location.href = '/portal';
                    return;
                }
                verifyEmailInput.value = email;
                if (data && data.verification_token) {
                    verifyTokenInput.value = data.verification_token;
                }
                setMode(VERIFY_MODE);
                setInfo((data && data.message) || 'Akun dibuat. Lanjut verifikasi email.');
            } catch (_) {
                setError('Network error. Coba lagi.');
            } finally {
                signupBtn.disabled = false;
                signupBtn.textContent = 'Create Account';
            }
        });

        forgotForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            resetNotice();
            const email = forgotEmailInput.value.trim();
            if (!email) {
                setError('Email wajib diisi.');
                return;
            }
            forgotBtn.disabled = true;
            forgotBtn.textContent = 'Submitting...';
            try {
                const res = await authRequest('/api/password/forgot', { email });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    setError((data && data.message) || 'Gagal request reset.');
                    return;
                }
                if (data && data.reset_token) {
                    resetTokenInput.value = data.reset_token;
                    setMode(RESET_MODE);
                    setInfo('Reset token dev diterbitkan. Lanjut ganti password.');
                    return;
                }
                setInfo((data && data.message) || 'Jika email terdaftar, token reset sudah diterbitkan.');
            } catch (_) {
                setError('Network error. Coba lagi.');
            } finally {
                forgotBtn.disabled = false;
                forgotBtn.textContent = 'Request Reset Token';
            }
        });

        resetForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            resetNotice();
            const token = resetTokenInput.value.trim();
            const pass = resetPasswordInput.value;
            const confirm = resetPasswordConfirmInput.value;
            if (!token || !pass) {
                setError('Token dan password wajib diisi.');
                return;
            }
            if (pass.length < 8) {
                setError('Password minimal 8 karakter.');
                return;
            }
            if (pass !== confirm) {
                setError('Konfirmasi password tidak sama.');
                return;
            }
            resetBtn.disabled = true;
            resetBtn.textContent = 'Resetting...';
            try {
                const res = await authRequest('/api/password/reset', { token, new_password: pass });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    setError((data && data.message) || 'Reset password gagal.');
                    return;
                }
                setMode(SIGNIN_MODE);
                setInfo('Password berhasil direset. Silakan sign in.');
                signinEmailInput.value = forgotEmailInput.value.trim();
            } catch (_) {
                setError('Network error. Coba lagi.');
            } finally {
                resetBtn.disabled = false;
                resetBtn.textContent = 'Reset Password';
            }
        });

        verifyRequestForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            resetNotice();
            const email = verifyEmailInput.value.trim();
            verifyRequestBtn.disabled = true;
            verifyRequestBtn.textContent = 'Submitting...';
            try {
                const res = await authRequest('/api/email/verify/request', { email });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    setError((data && data.message) || 'Gagal request verifikasi.');
                    return;
                }
                if (data && data.verification_token) {
                    verifyTokenInput.value = data.verification_token;
                }
                setInfo((data && data.message) || 'Jika email valid, token verifikasi sudah diterbitkan.');
            } catch (_) {
                setError('Network error. Coba lagi.');
            } finally {
                verifyRequestBtn.disabled = false;
                verifyRequestBtn.textContent = 'Request Verify Token';
            }
        });

        verifyConfirmForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            resetNotice();
            const token = verifyTokenInput.value.trim();
            if (!token) {
                setError('Verification token wajib diisi.');
                return;
            }
            verifyConfirmBtn.disabled = true;
            verifyConfirmBtn.textContent = 'Confirming...';
            try {
                const res = await authRequest('/api/email/verify/confirm', { token });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    setError((data && data.message) || 'Verifikasi gagal.');
                    return;
                }
                setMode(SIGNIN_MODE);
                setInfo((data && data.message) || 'Email berhasil diverifikasi. Silakan sign in.');
                if (verifyEmailInput.value.trim()) signinEmailInput.value = verifyEmailInput.value.trim();
            } catch (_) {
                setError('Network error. Coba lagi.');
            } finally {
                verifyConfirmBtn.disabled = false;
                verifyConfirmBtn.textContent = 'Confirm Email Verification';
            }
        });

        setMode(initialMode, false);
        if (prefillToken && !verifyTokenInput.value.trim()) verifyTokenInput.value = prefillToken;
        if (prefillEmail && !verifyEmailInput.value.trim()) verifyEmailInput.value = prefillEmail;
        redirectIfLoggedIn();
