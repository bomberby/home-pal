// General all-app code goes here
const getCookieValue = (name) => (
  document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)')?.pop() || ''
)



const lightToggle = document.getElementById("light-toggle");
lightToggle.addEventListener("click", () => {
  fetch('/sh/led/toggle')
});

const rainbowToggle = document.getElementById("rainbow-toggle");
rainbowToggle.addEventListener("click", () => {
  fetch('/sh/led/mode/rainbow/toggle')
});