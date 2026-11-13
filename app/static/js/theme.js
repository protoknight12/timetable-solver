(() => {
  const root = document.documentElement;
  const button = document.getElementById("theme-toggle");
  const icon = document.getElementById("theme-icon");
  const label = document.getElementById("theme-label");
  if (!button) return;

  function render(theme) {
    const toDark = theme === "light";
    icon.className = toDark ? "bi bi-moon-stars" : "bi bi-sun";
    label.textContent = toDark ? "Тъмна тема" : "Светла тема";
  }

  button.addEventListener("click", () => {
    const next = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-bs-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) {}
    render(next);
  });

  render(root.getAttribute("data-bs-theme"));
})();