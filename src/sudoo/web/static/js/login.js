// The sign-in page.
const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const submit = document.getElementById("login-submit");

fetch("/api/contact").then((r) => r.json()).then((d) => {
  document.getElementById("login-contact").textContent = `Forgot your password or cannot sign in? ${d.sentence}`;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  submit.disabled = true;
  submit.textContent = "Signing in...";
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: form.username.value.trim(), password: form.password.value }),
  });
  if (response.ok) {
    window.location.replace("/");
    return;
  }
  const data = await response.json();
  errorBox.textContent = data.error;
  errorBox.hidden = false;
  form.password.value = "";
  form.password.focus();
  submit.disabled = false;
  submit.textContent = "Sign in";
});
