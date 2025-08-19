// static/js/script.js
document.addEventListener('DOMContentLoaded', function() {
    // Select all accordion header buttons
    var accordionHeaders = document.querySelectorAll('.accordion-header');

    accordionHeaders.forEach(function(header) {
        header.addEventListener('click', function() {
            // Get the corresponding content panel for this header
            var content = this.nextElementSibling; // The div with class accordion-content

            // Check if the current header is already active (i.e., its panel is open)
            var isActive = this.classList.contains('active');

            // --- Close all other open accordion items ---
            // Find all currently active headers
            document.querySelectorAll('.accordion-header.active').forEach(function(activeHeader) {
                // If it's not the header that was just clicked
                if (activeHeader !== header) {
                    activeHeader.classList.remove('active'); // Remove active class from header
                    var activeContent = activeHeader.nextElementSibling;
                    activeContent.classList.remove('is-expanded'); // Remove expanded class from panel
                    activeContent.style.maxHeight = null; // Collapse the panel
                    activeContent.style.padding = '0 30px'; // Reset padding to match initial state
                }
            });

            // --- Toggle the clicked accordion item ---
            if (isActive) {
                // If it was active, close it
                this.classList.remove('active');
                content.classList.remove('is-expanded');
                content.style.maxHeight = null; // Collapse height
                content.style.padding = '0 30px'; // Reset padding
            } else {
                // If it was not active, open it
                this.classList.add('active');
                content.classList.add('is-expanded');
                // Set max-height to scrollHeight to allow CSS transition to work smoothly
                // Add a small buffer (e.g., 20px) to scrollHeight to prevent content clipping
                content.style.maxHeight = (content.scrollHeight + 20) + "px";
                content.style.padding = '25px 30px'; // Apply padding when opened
            }
        });
    });
});
