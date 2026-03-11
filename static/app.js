window.getToken = () => localStorage.getItem('dp_token');
window.clearAuth = () => {
    localStorage.removeItem('dp_token');
    localStorage.removeItem('dp_role');
    localStorage.removeItem('dp_username');
};

window.authFetch = async (url, options = {}) => {
    const token = window.getToken();
    options.headers = { ...(options.headers || {}), Authorization: 'Bearer ' + token };
    const res = await fetch(url, options);
    if (res.status === 401) {
        window.clearAuth();
        window.location.href = '/login';
    }
    return res;
};

window.ensureUiShell = () => {
    if (!document.getElementById('ui-toast-root')) {
        const toastRoot = document.createElement('div');
        toastRoot.id = 'ui-toast-root';
        toastRoot.className = 'ui-toast-root';
        document.body.appendChild(toastRoot);
    }
    if (!document.getElementById('ui-confirm-root')) {
        const confirmRoot = document.createElement('div');
        confirmRoot.id = 'ui-confirm-root';
        confirmRoot.className = 'ui-confirm-root';
        document.body.appendChild(confirmRoot);
    }
};

window.showToast = (message, type = 'info', duration = 3200) => {
    window.ensureUiShell();
    const root = document.getElementById('ui-toast-root');
    const el = document.createElement('div');
    el.className = `ui-toast ui-toast-${type}`;
    el.innerHTML = `<div class="ui-toast-dot"></div><div class="ui-toast-text"></div>`;
    el.querySelector('.ui-toast-text').textContent = message;
    root.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
        el.classList.remove('show');
        setTimeout(() => el.remove(), 220);
    }, duration);
};

window.showConfirm = ({ title = 'Please confirm', message = 'Are you sure?', confirmText = 'Confirm', cancelText = 'Cancel', danger = false }) => {
    window.ensureUiShell();
    const root = document.getElementById('ui-confirm-root');
    return new Promise((resolve) => {
        root.innerHTML = `
            <div class="ui-confirm-backdrop show">
                <div class="ui-confirm-card">
                    <div class="ui-confirm-title">${title}</div>
                    <div class="ui-confirm-message">${message}</div>
                    <div class="ui-confirm-actions">
                        <button class="ui-confirm-btn ui-confirm-cancel">${cancelText}</button>
                        <button class="ui-confirm-btn ${danger ? 'ui-confirm-danger' : 'ui-confirm-primary'}">${confirmText}</button>
                    </div>
                </div>
            </div>`;
        const backdrop = root.querySelector('.ui-confirm-backdrop');
        const close = (value) => {
            backdrop.classList.remove('show');
            setTimeout(() => { root.innerHTML = ''; resolve(value); }, 150);
        };
        root.querySelector('.ui-confirm-cancel').onclick = () => close(false);
        root.querySelector(danger ? '.ui-confirm-danger' : '.ui-confirm-primary').onclick = () => close(true);
        backdrop.onclick = (event) => { if (event.target === backdrop) close(false); };
    });
};

window.copyText = async (text, successMessage = 'Copied to clipboard.') => {
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
        } else {
            const area = document.createElement('textarea');
            area.value = text;
            area.style.position = 'fixed';
            area.style.opacity = '0';
            document.body.appendChild(area);
            area.select();
            document.execCommand('copy');
            area.remove();
        }
        window.showToast(successMessage, 'success');
        return true;
    } catch (error) {
        window.showToast('Copy failed. Please try again.', 'error');
        return false;
    }
};

