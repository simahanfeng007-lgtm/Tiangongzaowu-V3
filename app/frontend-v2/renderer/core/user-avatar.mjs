const DEFAULT_USER_AVATAR_SRC = "../assets/tiangong-avatar.png";

export function userAvatarSource(settings = {}) {
  return String(settings?.userAvatarDataUrl || "").trim() || DEFAULT_USER_AVATAR_SRC;
}

export function renderUserAvatar(target, settings = {}, options = {}) {
  if (!target) return;
  target.replaceChildren();
  const img = document.createElement("img");
  img.src = userAvatarSource(settings);
  img.alt = String(options.alt || "用户头像");
  img.className = String(options.className || "user-avatar-img");
  img.addEventListener("error", () => {
    if (img.src.endsWith(DEFAULT_USER_AVATAR_SRC)) {
      target.replaceChildren();
      target.textContent = String(options.fallbackGlyph || "你");
      return;
    }
    img.src = DEFAULT_USER_AVATAR_SRC;
  });
  target.appendChild(img);
}
