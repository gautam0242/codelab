const box = document.getElementById("tests");
document.getElementById("addt").onclick = () => { const c = box.querySelector(".trow").cloneNode(true); c.querySelectorAll("textarea").forEach(t => t.value = ""); c.querySelector("select").value = "0"; box.appendChild(c); };
box.addEventListener("click", e => { if (e.target.classList.contains("rm") && box.querySelectorAll(".trow").length > 1) e.target.closest(".trow").remove(); });
