// Advanced Portfolio Interactions & Cyber Glow Animations

// Smooth scrolling for navigation links
document.querySelectorAll('a[href^="#"]').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    document.querySelector(link.getAttribute('href')).scrollIntoView({
      behavior: 'smooth'
    });
  });
});

// Fade + slide animation on scroll
const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
    }
  });
}, { threshold: 0.2 });

document.querySelectorAll('.section').forEach(section => {
  section.classList.add('fade-section');
  observer.observe(section);
});

// Typewriter effect for header subtitle
function typeWriter(element, text, speed = 70) {
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
