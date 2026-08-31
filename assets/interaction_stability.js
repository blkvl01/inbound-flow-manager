(function () {
    'use strict';

    function markPending(button, label) {
        if (!button || button.dataset.actionPending === '1') return false;
        button.dataset.actionPending = '1';
        if (!button.dataset.idleText) button.dataset.idleText = button.textContent || '';
        button.classList.add('action-pending');
        if (label) button.textContent = label;
        setTimeout(function () {
            if (!button.isConnected) return;
            button.disabled = true;
        }, 0);
        setTimeout(function () {
            if (!button.isConnected) return;
            button.dataset.actionPending = '0';
            button.disabled = false;
            button.classList.remove('action-pending');
            if (button.dataset.idleText) button.textContent = button.dataset.idleText;
        }, 2600);
        return true;
    }

    document.addEventListener('click', function (event) {
        var storeButton = event.target.closest('.badge-stored-empty, .badge-stored-active, .truck-store-btn');
        if (storeButton) {
            if (storeButton.dataset.actionPending === '1') {
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            markPending(storeButton, 'Mentés...');
            return;
        }

        var refreshButton = event.target.closest('#refresh-btn');
        if (refreshButton) {
            markPending(refreshButton, 'Folyamatban...');
        }
    }, true);
})();
