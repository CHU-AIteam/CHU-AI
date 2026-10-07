(() => {
  "use strict";

  // One gentle flash, not a strobe. The entire layer is non-modal and pointer-transparent.
  // Reading time is independent of motion preference: keep the award legible before fading.
  const TIMING = Object.freeze({ flash: 3200, reveal: 4800, reaction: 6500, encore: 13000, finale: 21000, settle: 31000, finish: 33000 });
  const CONFETTI_COUNT = 156;

  class ComoFortuneCelebration {
    constructor(element) {
      this.element = element;
      this.timerIds = new Set();
      this.runId = 0;
      const particles = element.querySelector(".fortune-particles");
      const fragment = document.createDocumentFragment();
      for (let index = 0; index < CONFETTI_COUNT; index += 1) {
        const particle = document.createElement("i");
        particle.className = "fortune-particle";
        particle.style.setProperty("--x", `${7 + (index * 19) % 86}%`);
        particle.style.setProperty("--y", `${12 + (index * 29) % 70}%`);
        particle.style.setProperty("--dx", `${Math.sin(index * 2.4) * 210}px`);
        particle.style.setProperty("--dy", `${130 + (index * 31) % 250}px`);
        particle.style.setProperty("--spin", `${index % 2 ? 220 : -180}deg`);
        particle.style.setProperty("--delay", `${(index % 13) * 40}ms`);
        particle.style.setProperty("--size", `${8 + (index % 5) * 4}px`);
        fragment.appendChild(particle);
      }
      particles.appendChild(fragment);
      for (const wave of ["encore", "finale"]) {
        const container = element.querySelector(`.fortune-fireworks-${wave}`);
        for (const [burst, position] of [[18, 24], [76, 18], [83, 63]].entries()) {
          const firework = document.createElement("div");
          firework.className = "fortune-firework";
          firework.style.setProperty("--burst-x", `${position[0]}%`);
          firework.style.setProperty("--burst-y", `${position[1]}%`);
          firework.style.setProperty("--burst-delay", `${burst * 220}ms`);
          for (let ray = 0; ray < 18; ray += 1) {
            const spark = document.createElement("i");
            spark.className = "fortune-firework-spark";
            spark.style.setProperty("--angle", `${ray * 20}deg`);
            firework.appendChild(spark);
          }
          container.appendChild(firework);
        }
      }
      window.addEventListener("pagehide", () => this.cancel());
      window.addEventListener("resize", () => {
        if (!this.element.hidden) this.alignLightOrigin();
      });
    }

    alignLightOrigin() {
      const award = this.element.querySelector(".fortune-award");
      const title = this.element.querySelector(".fortune-award-title");
      // Layout offsets ignore the award's entry animation: all light stays centered
      // on the final title, even during the reveal or a viewport resize.
      const x = award.offsetLeft + title.offsetLeft + title.offsetWidth / 2;
      const y = award.offsetTop + title.offsetTop + title.offsetHeight / 2;
      this.element.style.setProperty("--fortune-origin-x", `${x}px`);
      this.element.style.setProperty("--fortune-origin-y", `${y}px`);
    }

    schedule(callback, delay, runId) {
      const id = window.setTimeout(() => {
        this.timerIds.delete(id);
        if (this.runId === runId) {
          callback();
        }
      }, delay);
      this.timerIds.add(id);
    }

    play(result, { reduceMotion = false, onReveal, onReaction, onEncore } = {}) {
      this.cancel();
      const runId = this.runId;
      const timing = TIMING;
      const element = this.element;
      element.querySelector(".fortune-award-title").textContent = result.label;
      element.querySelector(".fortune-award-message").textContent = result.message;
      element.hidden = false;
      element.dataset.phase = "charge";
      this.alignLightOrigin();
      this.schedule(() => {
        element.dataset.phase = "flash";
        element.dataset.lit = "true";
      }, timing.flash, runId);
      this.schedule(() => {
        element.dataset.phase = "reveal";
        element.dataset.revealed = "true";
        onReveal?.();
      }, timing.reveal, runId);
      this.schedule(() => {
        element.dataset.phase = "celebrate";
        element.dataset.celebrating = "true";
        onReaction?.();
      }, timing.reaction, runId);
      this.schedule(() => {
        element.dataset.encore = "true";
        if (!reduceMotion) onEncore?.();
      }, timing.encore, runId);
      this.schedule(() => {
        element.dataset.finale = "true";
      }, timing.finale, runId);
      this.schedule(() => {
        element.dataset.phase = "settle";
      }, timing.settle, runId);
      this.schedule(() => this.cancel(), timing.finish, runId);
    }

    cancel() {
      this.runId += 1;
      for (const id of this.timerIds) {
        window.clearTimeout(id);
      }
      this.timerIds.clear();
      this.element.hidden = true;
      for (const key of ["phase", "lit", "revealed", "celebrating", "encore", "finale"]) {
        delete this.element.dataset[key];
      }
    }
  }

  window.ComoFortuneCelebration = ComoFortuneCelebration;
})();
