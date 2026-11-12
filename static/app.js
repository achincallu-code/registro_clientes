document.addEventListener("DOMContentLoaded", () => {
  const dateElement = document.querySelector("[data-current-date]");
  if (dateElement) {
    dateElement.textContent = new Intl.DateTimeFormat("es-MX", {
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
    }).format(new Date());
  }

  const greeting = document.querySelector("[data-greeting]");
  if (greeting) {
    const hour = new Date().getHours();
    greeting.textContent = hour < 12 ? "Buenos días" : hour < 19 ? "Buenas tardes" : "Buenas noches";
  }

  document.querySelectorAll('input[name="telefono"]').forEach((input) => {
    input.addEventListener("input", () => {
      input.value = input.value.replace(/\D/g, "").slice(0, 10);
    });
  });

  document.querySelectorAll("[data-photo-input]").forEach((input) => {
    input.addEventListener("change", () => {
      if (!(input instanceof HTMLInputElement) || !input.files?.[0]) return;
      const file = input.files[0];
      const preview = input.closest(".photo-picker")?.querySelector("[data-photo-preview]");
      const name = input.closest(".photo-picker")?.querySelector("[data-photo-name], strong");
      if (file.size > 5 * 1024 * 1024) {
        input.value = "";
        window.alert("La fotografía no puede superar los 5 MB.");
        return;
      }
      if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
        input.value = "";
        window.alert("Elige una imagen JPG, PNG o WebP.");
        return;
      }
      if (preview) {
        const image = document.createElement("img");
        image.alt = "";
        image.src = URL.createObjectURL(file);
        preview.replaceChildren(image);
      }
      if (name) name.textContent = file.name;
      const removePhoto = input.closest(".field")?.querySelector("[data-remove-photo]");
      if (removePhoto instanceof HTMLInputElement) removePhoto.checked = false;
    });
  });

  document.querySelectorAll("[data-remove-photo]").forEach((input) => {
    if (!(input instanceof HTMLInputElement)) return;
    const field = input.closest(".field");
    const preview = field?.querySelector("[data-photo-preview]");
    if (!preview) return;
    const photoInput = field.querySelector("[data-photo-input]");
    const originalPreview = Array.from(preview.childNodes, (node) => node.cloneNode(true));

    input.addEventListener("change", () => {
      if (input.checked && photoInput instanceof HTMLInputElement && photoInput.files?.length) {
        input.checked = false;
        return;
      }
      if (input.checked) {
        preview.textContent = "C";
      } else {
        preview.replaceChildren(...originalPreview.map((node) => node.cloneNode(true)));
      }
    });
  });

  const sidebar = document.querySelector("#sidebar");
  const menuToggle = document.querySelector("[data-sidebar-toggle]");
  const backdrop = document.querySelector("[data-sidebar-close]");
  const closeMenu = () => {
    sidebar?.classList.remove("is-open");
    backdrop?.classList.remove("is-visible");
    menuToggle?.setAttribute("aria-expanded", "false");
    document.body.classList.remove("menu-open");
  };
  menuToggle?.addEventListener("click", () => {
    const isOpen = sidebar?.classList.toggle("is-open") ?? false;
    backdrop?.classList.toggle("is-visible", isOpen);
    menuToggle.setAttribute("aria-expanded", String(isOpen));
    document.body.classList.toggle("menu-open", isOpen);
  });
  backdrop?.addEventListener("click", closeMenu);
  sidebar?.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeMenu));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });

  document.querySelectorAll(".alert-dismiss").forEach((button) => {
    button.addEventListener("click", () => button.closest(".alert")?.remove());
  });

  document.querySelectorAll("[data-confirm-delete]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const name = form.getAttribute("data-client-name") || "este cliente";
      if (!window.confirm(`¿Eliminar a ${name}? Esta acción no se puede deshacer.`)) {
        event.preventDefault();
      }
    });
  });

  const clientForm = document.querySelector("[data-client-form]");
  clientForm?.addEventListener("submit", () => {
    const submitButton = clientForm.querySelector('button[type="submit"]');
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.classList.add("is-submitting");
      submitButton.setAttribute("aria-busy", "true");
      submitButton.disabled = true;
    }
  });

  const searchForm = document.querySelector("[data-directory-search-form]");
  const searchInput = document.querySelector("[data-directory-search]");
  const cards = Array.from(document.querySelectorAll("[data-contact-card]"));
  const liveEmpty = document.querySelector("[data-live-empty]");
  const visibleCount = document.querySelector("[data-visible-count]");
  const countLabel = document.querySelector("[data-count-label]");
  if (searchForm instanceof HTMLFormElement && searchInput instanceof HTMLInputElement) {
    let submitTimer;
    const serverFiltered = new URLSearchParams(window.location.search).has("q");
    searchInput.addEventListener("input", () => {
      const query = searchInput.value.trim().normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase();
      let shown = 0;
      cards.forEach((card) => {
        const content = (card.getAttribute("data-search") || "")
          .normalize("NFD")
          .replace(/[\u0300-\u036f]/g, "")
          .toLocaleLowerCase();
        const matches = content.includes(query);
        card.toggleAttribute("hidden", !matches);
        if (matches) shown += 1;
      });
      liveEmpty?.toggleAttribute("hidden", shown !== 0);
      if (visibleCount) visibleCount.textContent = String(shown);
      if (countLabel) countLabel.textContent = shown === 1 ? "cliente" : "clientes";

      if (serverFiltered) {
        window.clearTimeout(submitTimer);
        submitTimer = window.setTimeout(() => searchForm.requestSubmit(), 350);
      }
    });
  }
});
