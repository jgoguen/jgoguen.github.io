// Displays and updates the post scrollbar

const progressBar = document.querySelector("#progress-bar");
const scrollToTop = document.querySelector("#scroll-to-top");

const scrollContainer = () => {
  return document.documentElement || document.body;
};

document.addEventListener("scroll", () => {
  const pctDone = (scrollContainer().scrollTop / (scrollContainer().scrollHeight - scrollContainer().clientHeight)) * 100;
  progressBar.style.width = `${pctDone}%`;

  // Show the progress bar once the page has scrolled 100px
  if (scrollContainer().scrollTop > 100) {
    progressBar.classList.remove("hidden");
    scrollToTop.classList.remove("hidden");
  } else {
    progressBar.classList.add("hidden");
    scrollToTop.classList.add("hidden");
  }
});

scrollToTop.addEventListener("click", () => {
  document.body.scrollIntoView({
    behavior: "smooth",
  });
});
