// FE-20: the "add constraint" modal. HTMX swaps the form into #constraint-modal-content;
// this file opens the modal and runs the target picker (search, select all, clear, count).
(() => {
  const modalEl = document.getElementById("constraint-modal");
  const content = document.getElementById("constraint-modal-content");
  if (!modalEl || !content || !window.bootstrap) return;

  const items = (picker) => [...picker.querySelectorAll("[data-picker-item]")];
  const box = (item) => item.querySelector("input[type=checkbox]");

  const updateCount = (picker) => {
    const all = items(picker);
    const chosen = all.filter((i) => box(i).checked).length;
    picker.querySelector("[data-picker-count]").textContent = `Избрани: ${chosen} от ${all.length}`;
  };

  const filter = (picker) => {
    const q = picker.querySelector("[data-picker-search]").value.trim().toLocaleLowerCase("bg");
    let shown = 0;
    items(picker).forEach((i) => {
      const match = i.textContent.toLocaleLowerCase("bg").includes(q);
      i.hidden = !match;
      if (match) shown += 1;
    });
    picker.querySelector("[data-picker-empty]").hidden = shown > 0;
  };

  content.addEventListener("input", (e) => {
    const picker = e.target.closest("[data-picker]");
    if (picker && e.target.matches("[data-picker-search]")) filter(picker);
  });
  content.addEventListener("change", (e) => {
    const picker = e.target.closest("[data-picker]");
    if (picker && e.target.matches("input[type=checkbox]")) updateCount(picker);
  });
  content.addEventListener("keydown", (e) => {
    // Enter in the search box must not submit the form.
    if (e.key === "Enter" && e.target.matches("[data-picker-search]")) e.preventDefault();
  });
  content.addEventListener("click", (e) => {
    const picker = e.target.closest("[data-picker]");
    if (!picker) return;
    if (e.target.closest("[data-picker-all]")) {
      // Only the rows the search currently shows.
      items(picker).forEach((i) => { if (!i.hidden) box(i).checked = true; });
      updateCount(picker);
    } else if (e.target.closest("[data-picker-clear]")) {
      items(picker).forEach((i) => { box(i).checked = false; });
      updateCount(picker);
    }
  });

  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.detail.target !== content) return;
    const modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
    const error = content.querySelector("[data-modal-error]");
    if (modalEl.classList.contains("show")) {
      if (error) error.focus(); // the form was re-rendered with errors
    } else {
      modal.show();
    }
  });
  modalEl.addEventListener("shown.bs.modal", () => {
    const first = content.querySelector("[data-picker-search], input:not([type=hidden]), select");
    if (first) first.focus();
  });
  modalEl.addEventListener("hidden.bs.modal", () => {
    content.innerHTML = "";
    const kind = document.getElementById("constraint-kind");
    if (kind) kind.focus();
  });
})();
