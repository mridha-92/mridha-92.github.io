// Advanced Portfolio Interactions & Cyber Glow Animations

// Scroll progress bar
const progressBar = document.getElementById('progress-bar');
function updateProgress() {
  const scrollTop = document.documentElement.scrollTop || document.body.scrollTop;
  const height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
  progressBar.style.width = (height > 0 ? (scrollTop / height) * 100 : 0) + '%';
}
window.addEventListener('scroll', updateProgress);
updateProgress();

// Smooth scrolling for navigation links
document.querySelectorAll('a[href^="#"]').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    const target = document.querySelector(link.getAttribute('href'));
    if (target) target.scrollIntoView({ behavior: 'smooth' });
  });
});

// Fade + slide animation on scroll
const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
    }
  });
}, { threshold: 0.15 });

document.querySelectorAll('.section').forEach(section => {
  section.classList.add('fade-section');
  observer.observe(section);
});

// Active nav link highlighting
const sections = document.querySelectorAll('section[id], header[id]');
const navLinks = document.querySelectorAll('.nav-links a');
window.addEventListener('scroll', () => {
  let current = '';
  sections.forEach(section => {
    if (window.scrollY >= section.offsetTop - 120) {
      current = section.getAttribute('id');
    }
  });
  navLinks.forEach(link => {
    link.classList.toggle('active', link.getAttribute('href') === '#' + current);
  });
});

// Typewriter effect for header subtitle
function typeWriter(element, text, speed = 60) {
  let i = 0;
  function typing() {
    if (i < text.length) {
      element.innerHTML += text.charAt(i);
      i++;
      setTimeout(typing, speed);
    }
  }
  element.innerHTML = "";
  typing();
}

// Initialize typewriter
window.addEventListener('DOMContentLoaded', () => {
  const subtitle = document.querySelector('#subtitle');
  if (subtitle) {
    typeWriter(subtitle, subtitle.getAttribute('data-text'));
  }
});

// Cyber glow hover effects
document.querySelectorAll('.project-card').forEach(card => {
  card.addEventListener('mouseenter', () => {
    card.classList.add('glow');
  });
  card.addEventListener('mouseleave', () => {
    card.classList.remove('glow');
  });
});
