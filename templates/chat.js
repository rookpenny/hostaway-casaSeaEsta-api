const chatBox = document.getElementById("chat-box");
const inputField = document.getElementById("user-input");
const fileUpload = document.getElementById("file-upload");
const chatSound = document.getElementById("chat-sound");
let uploadedImage = null;

function toggleDarkMode() {
  document.body.classList.toggle("dark");
  const toggleBtn = document.getElementById("dark-toggle");
  toggleBtn.textContent = document.body.classList.contains("dark") ? "Light Mode" : "Dark Mode";
}

function scrollCarousel(direction) {
  const el = document.getElementById("trend-carousel");
  el.scrollLeft += direction * 200;
}

function addMessage(content, sender) {
  const msg = document.createElement("div");
  msg.className = `msg ${sender}`;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = content;
  msg.appendChild(avatar);
  msg.appendChild(bubble);
  chatBox.appendChild(msg);
  chatBox.scrollTop = chatBox.scrollHeight;
  if (sender === "bot") chatSound.play();
}

function handleKey(e) {
  if (e.key === "Enter") sendMessage();
}

async function sendMessage() {
  const userMsg = inputField.value.trim();
  if (!userMsg && !uploadedImage) return;

  let content = "";
  if (userMsg) content += `<div>${userMsg}</div>`;
  if (uploadedImage) content += `<img src="${uploadedImage}" alt="uploaded image">`;
  addMessage(content, "user");
  inputField.value = "";

  addMessage(`<i>Sandy is thinking...</i><br><div class='typing-dots'><span></span><span></span><span></span></div>`, "bot");

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: userMsg })
    });
    const data = await res.json();
    chatBox.lastChild.remove();
    addMessage(data.response || "Hmm, not sure what that is 🌴", "bot");
  } catch {
    chatBox.lastChild.remove();
    addMessage("Something went wrong. Try again later. 🐚", "bot");
  }

  uploadedImage = null;
}

fileUpload.addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (event) => {
    uploadedImage = event.target.result;
    inputField.focus();
  };
  reader.readAsDataURL(file);
});
