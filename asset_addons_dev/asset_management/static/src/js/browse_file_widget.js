/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";

// Override translation for "Upload your file"
_t.cache = _t.cache || {};
_t.cache["Upload your file"] = "Browse your file";
_t.cache["upload your file"] = "browse your file";
_t.cache["Upload your file "] = "Browse your file ";

// Also add to the translations object if it exists
if (_t.translations) {
    _t.translations["Upload your file"] = "Browse your file";
    _t.translations["upload your file"] = "browse your file";
}

// Function to update button text
function updateButtonText() {
    // Target all possible button selectors
    const selectors = [
        '.o_installer_upload_zone button',
        '.o_installer_upload_zone .o_form_label',
        '.o_installer_upload_zone label',
        '.o_installer_upload_zone .o_select_file_button',
        'button:contains("Upload your file")',
        '.o_binary_uploader button',
        '.o_binary_uploader .o_form_label'
    ];
    
    selectors.forEach(selector => {
        try {
            document.querySelectorAll(selector).forEach(function(btn) {
                const text = btn.textContent.trim();
                if (text.includes('Upload your file') || text.includes('upload your file')) {
                    btn.textContent = 'Browse your file';
                }
            });
        } catch(e) {
            // Ignore invalid selectors
        }
    });
}

// Watch for DOM changes and replace text
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

function init() {
    // Run immediately
    updateButtonText();
    
    // Run periodically to catch late-loaded buttons
    const interval = setInterval(updateButtonText, 200);
    
    // Use MutationObserver for real-time updates
    const observer = new MutationObserver(function(mutations) {
        let shouldUpdate = false;
        mutations.forEach(function(mutation) {
            if (mutation.addedNodes.length > 0) {
                shouldUpdate = true;
            }
        });
        if (shouldUpdate) {
            updateButtonText();
        }
    });
    
    observer.observe(document.body, { 
        childList: true, 
        subtree: true,
        characterData: false
    });
    
    // Also watch for form view changes
    document.addEventListener('click', function(e) {
        if (e.target.closest('.o_form_view') || e.target.closest('.o_list_view')) {
            setTimeout(updateButtonText, 50);
            setTimeout(updateButtonText, 200);
            setTimeout(updateButtonText, 500);
        }
    });
    
    // Watch for URL changes (Odoo SPA navigation)
    let lastUrl = location.href;
    new MutationObserver(() => {
        const url = location.href;
        if (url !== lastUrl) {
            lastUrl = url;
            setTimeout(updateButtonText, 100);
            setTimeout(updateButtonText, 300);
        }
    }).observe(document, {subtree: true, childList: true});
}

// Export for potential use elsewhere
export const BrowseFileWidget = {
    updateButtonText
};
