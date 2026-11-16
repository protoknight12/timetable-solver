// Drag a lesson onto another cell: fill the hidden form and post it (the server checks the move).
(() => {
  const form = document.getElementById("drop-form");
  if (!form) return;
  let dragged = null;
  const cell = (e) => e.target.closest("td[data-day]");

  document.addEventListener("dragstart", (e) => {
    dragged = e.target.closest ? e.target.closest("[data-move-url]") : null;
    if (dragged) {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", ""); // Firefox needs data to start a drag
    }
  });
  document.addEventListener("dragend", () => { dragged = null; });
  document.addEventListener("dragover", (e) => {
    if (dragged && cell(e)) e.preventDefault();
  });
  document.addEventListener("drop", (e) => {
    const td = cell(e);
    if (!dragged || !td) return;
    e.preventDefault();
    form.action = dragged.dataset.moveUrl;
    form.elements.day.value = td.dataset.day;
    form.elements.start_period.value = td.dataset.period;
    form.submit();
  });
})();
