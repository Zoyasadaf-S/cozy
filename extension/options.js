const ORIGINS = { origins: ["<all_urls>"] };
async function refresh() {
  const on = await chrome.permissions.contains(ORIGINS);
  document.getElementById("state").textContent = on ? "Page reading is ON." : "Page reading is OFF.";
}
document.getElementById("grant").onclick = async () => { await chrome.permissions.request(ORIGINS); refresh(); };
document.getElementById("revoke").onclick = async () => { await chrome.permissions.remove(ORIGINS); refresh(); };
refresh();
