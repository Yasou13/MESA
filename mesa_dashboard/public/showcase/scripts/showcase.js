// Bilingual implementation summary; no synthetic benchmark or PASS telemetry.
document.querySelectorAll('[data-language]').forEach((button) => {
    button.addEventListener('click', () => {
        const language = button.dataset.language;
        document.documentElement.lang = language;
        document.querySelectorAll('[data-language-content]').forEach((article) => {
            article.hidden = article.dataset.languageContent !== language;
        });
    });
});
