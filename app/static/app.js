"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const log = $("log"), scroller = $("scroller"), input = $("input"), sendBtn = $("sendBtn"), dialog = $("pwDialog");

  const EXAMPLES = [
    "Profilni sozlaymiz",
    "Korporativ buyurtmachi: 180 mln so'mlik 20 ta noutbuk — qaysi xarid usuli?",
    "Suyuq sovun uchun klassifikator kodini top",
    "Narx tahlili: 3 manba — 185 000, 172 000 (40 dona), 178 000 (30 dona); miqdor 40",
    "Ofis stuli uchun raqobatni cheklamaydigan texnik topshiriq tayyorla",
    "To'g'ridan-to'g'ri shartnoma qachon mumkin? (O'RQ-684 71-modda)",
  ];
  const fmtList = (a) => (Array.isArray(a) && a.length ? ": " + a.map((s) => `"${s}"`).join(", ") : "");
  const TOOL_LABELS = {
    kod_qidir: (i) => "Klassifikator qidiruvi" + fmtList(i.sozlar) + (i.kod ? ` (kod ${i.kod}*)` : ""),
    narx_tahlili: (i) => `Narx tahlili: ${(i.manbalar || []).length} ta manba, miqdor ${i.miqdor ?? "?"}`,
    bilim_royxati: () => "Bilim bazasi fayllari ro'yxati",
    bilim_oqish: (i) => `Fayl o'qilmoqda: ${i.yol || ""}${i.boshlanish > 1 ? ` (${i.boshlanish}-qatordan)` : ""}`,
    bilim_qidirish: (i) => `Bilim bazasida qidiruv: "${i.soz || ""}"${i.papka ? ` (${i.papka})` : ""}`,
  };
  const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];
  const MAX_IMAGES = 5;
  const MAX_SIDE = 2000; // API: ko'p rasmli so'rovda har rasm 2000 px dan oshmasligi kerak
  const RENDER_INTERVAL = 80; // ms — uzun javobda Markdown har belgida qayta chizilmasin

  let suhbatId = null, token = "", busy = false, controller = null, images = [];
  let generation = 0; // "Yangi suhbat" bosilganda oshadi: eski oqim hodisalari e'tiborga olinmaydi
  try { token = sessionStorage.getItem("kirish_token") || ""; } catch (_) { /* saqlash o'chirilgan */ }

  // ---------- yordamchilar ----------
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  const escapeHtml = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  function scrollDown(force) { if (force || nearBottom()) scroller.scrollTop = scroller.scrollHeight; }
  function showBanner(text) { const b = $("banner"); b.textContent = text; b.hidden = !text; }
  function headers() {
    const h = { "Content-Type": "application/json" };
    if (token) h["X-Kirish-Token"] = token;
    return h;
  }
  function setToken(t) {
    token = t || "";
    try { token ? sessionStorage.setItem("kirish_token", token) : sessionStorage.removeItem("kirish_token"); } catch (_) { /* e'tiborsiz */ }
  }
  async function errorDetail(r) {
    try {
      const j = await r.json();
      if (typeof j.detail === "string") return j.detail;
    } catch (_) { /* JSON emas */ }
    return r.status === 413 ? "So'rov juda katta — rasmlar sonini yoki hajmini kamaytiring." : `Xato (${r.status}). So'rov noto'g'ri.`;
  }

  // ---------- Markdown ----------
  const canMd = typeof marked !== "undefined" && typeof DOMPurify !== "undefined";
  if (canMd) {
    marked.use({
      gfm: true,
      breaks: false,
      renderer: {
        // Rasm yuklanmaydi: javobga (masalan, veb-sahifadagi yashirin ko'rsatma orqali) tushib qolgan
        // tashqi rasm manzili suhbat ma'lumotini begona serverga olib chiqib ketmasin.
        image(href, title, text) { return text ? escapeHtml(`[rasm: ${text}]`) : ""; },
        checkbox(checked) { return checked ? "☑ " : "☐ "; },
      },
    });
  }
  // Faqat matn bezagi uchun kerakli teglar: <img>, <style>, <form>, <input>, <iframe> va boshqalar o'tmaydi
  const PURIFY = {
    ALLOWED_TAGS: ["p", "br", "hr", "strong", "b", "em", "i", "del", "s", "u", "code", "pre", "blockquote", "ul", "ol", "li",
      "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "th", "td", "a", "sup", "sub"],
    ALLOWED_ATTR: ["href", "title", "align", "start", "colspan", "rowspan"],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  };
  function renderMd(node, text) {
    if (!canMd) {
      node.textContent = text;
      node.classList.add("plain");
      return;
    }
    node.innerHTML = DOMPurify.sanitize(marked.parse(text), PURIFY);
    node.querySelectorAll("a").forEach((a) => {
      if (/^(https?:|mailto:)/i.test(a.getAttribute("href") || "")) {
        a.target = "_blank";
        a.rel = "noopener noreferrer";
      } else {
        a.removeAttribute("href");
      }
    });
    node.querySelectorAll("pre").forEach((pre) => {
      const code = pre.textContent;
      const b = el("button", "copy", "Nusxa");
      b.type = "button";
      b.addEventListener("click", () => copyText(code, b));
      pre.appendChild(b);
    });
  }
  async function copyText(text, btn) {
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        ok = true;
      }
    } catch (_) { /* pastdagi zaxira usul */ }
    if (!ok) { // HTTPS bo'lmagan manzilda clipboard API ishlamaydi
      const ta = el("textarea", "offscreen");
      ta.value = text;
      ta.readOnly = true;
      document.body.appendChild(ta);
      ta.select();
      try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
      ta.remove();
    }
    const old = btn.textContent;
    btn.textContent = ok ? "Nusxalandi" : "Nusxalab bo'lmadi";
    setTimeout(() => { btn.textContent = old; }, 1500);
  }

  // ---------- holat va kirish ----------
  async function loadStatus() {
    try {
      const r = await fetch("/api/holat", { headers: headers() });
      const s = await r.json();
      if (!s.kirilgan) {
        setToken("");
        askPassword();
        return;
      }
      $("meta").textContent = `${s.model}${s.veb_qidiruv ? " · veb-qidiruv yoqilgan" : ""}`;
      if (!s.bilim) showBanner("Bilim bazasi yuklanmagan. Serverda: python3 scripts/bilim_yukla.py <skill papkasi yoki .skill fayli> — keyin serverni qayta ishga tushiring.");
      else if (!s.klassifikator) showBanner("Klassifikator bazasi (data/klassifikator.json) topilmadi — kod qidiruvi ishlamaydi.");
    } catch (_) {
      showBanner("Server bilan aloqa yo'q.");
    }
  }
  function askPassword(msg) {
    $("pwError").hidden = !msg;
    $("pwError").textContent = msg || "";
    if (!dialog.open) {
      $("pwInput").value = "";
      dialog.showModal();
    }
    $("pwInput").focus();
  }
  dialog.addEventListener("cancel", (e) => e.preventDefault()); // Esc bilan yopilmasin
  $("pwForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await fetch("/api/kirish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ parol: $("pwInput").value }) });
      if (!r.ok) {
        askPassword(await errorDetail(r));
        $("pwInput").select();
        return;
      }
      setToken((await r.json()).token);
      dialog.close();
      showBanner("");
      loadStatus();
      input.focus();
    } catch (_) {
      askPassword("Server bilan aloqa yo'q.");
    }
  });

  // ---------- namunalar ----------
  EXAMPLES.forEach((t) => {
    const b = el("button", null, t);
    b.type = "button";
    b.addEventListener("click", () => { input.value = t; autosize(); input.focus(); });
    $("examples").appendChild(b);
  });

  // ---------- rasmlar ----------
  $("attachBtn").addEventListener("click", () => $("file").click());
  $("file").addEventListener("change", (e) => { addFiles([...e.target.files]); e.target.value = ""; });
  input.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData ? e.clipboardData.files : [])].filter((f) => f.type.startsWith("image/"));
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  });
  async function addFiles(files) {
    showBanner("");
    for (const f of files) {
      if (images.length >= MAX_IMAGES) {
        showBanner(`Bitta xabarga ko'pi bilan ${MAX_IMAGES} ta rasm biriktirish mumkin.`);
        break;
      }
      try {
        images.push(await prepareImage(f));
      } catch (err) {
        showBanner(String((err && err.message) || err));
      }
      drawThumbs();
    }
  }
  function readAsDataURL(file) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.onerror = () => rej(new Error("Rasmni o'qib bo'lmadi."));
      r.readAsDataURL(file);
    });
  }
  function loadImage(url) {
    return new Promise((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = () => rej(new Error("Rasm ochilmadi — JPEG, PNG, WEBP yoki GIF fayl biriktiring."));
      i.src = url;
    });
  }
  async function prepareImage(file) {
    if (!IMAGE_TYPES.includes(file.type)) throw new Error("Faqat JPEG, PNG, WEBP yoki GIF rasm biriktirish mumkin.");
    const blobUrl = URL.createObjectURL(file);
    try {
      const img = await loadImage(blobUrl);
      const w = img.naturalWidth, h = img.naturalHeight;
      if (!w || !h) throw new Error("Rasm bo'sh.");
      // Kichik PNG (ekran nusxasi) sifati yo'qolmasligi uchun o'zgarishsiz yuboriladi
      if (file.type === "image/png" && Math.max(w, h) <= MAX_SIDE && file.size <= 2 * 1024 * 1024) {
        const url = await readAsDataURL(file);
        return { media_type: "image/png", data: url.split(",")[1], url };
      }
      // Qolganlari kichraytirilib JPEG ga o'giriladi: telefon surati bir necha MB bo'ladi;
      // chizish paytida brauzer suratning burilishini (EXIF) ham to'g'rilaydi.
      const k = Math.min(1, MAX_SIDE / Math.max(w, h));
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(w * k));
      c.height = Math.max(1, Math.round(h * k));
      const ctx = c.getContext("2d");
      ctx.fillStyle = "#fff"; // shaffof fon JPEG da qora bo'lib qolmasin
      ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(img, 0, 0, c.width, c.height);
      const url = c.toDataURL("image/jpeg", 0.9);
      return { media_type: "image/jpeg", data: url.split(",")[1], url };
    } finally {
      URL.revokeObjectURL(blobUrl);
    }
  }
  function drawThumbs() {
    const t = $("thumbs");
    t.replaceChildren();
    images.forEach((im, idx) => {
      const d = el("div", "thumb");
      const i = el("img");
      i.src = im.url;
      i.alt = `biriktirilgan rasm ${idx + 1}`;
      const x = el("button", null, "×");
      x.type = "button";
      x.title = "Olib tashlash";
      x.setAttribute("aria-label", `${idx + 1}-rasmni olib tashlash`);
      x.addEventListener("click", () => { images.splice(idx, 1); drawThumbs(); });
      d.append(i, x);
      t.appendChild(d);
    });
  }

  // ---------- yozish maydoni ----------
  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(200, input.scrollHeight) + "px";
  }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!busy) send();
    }
  });
  sendBtn.addEventListener("click", () => { if (busy) { if (controller) controller.abort(); } else send(); });
  function setBusy(b) {
    busy = b;
    sendBtn.textContent = b ? "To'xtatish" : "Yuborish";
    sendBtn.classList.toggle("primary", !b);
  }
  $("newBtn").addEventListener("click", () => {
    generation++;
    if (controller) controller.abort();
    controller = null;
    setBusy(false);
    const old = suhbatId;
    suhbatId = null;
    if (old) { // server xotirasini bo'shatamiz; javobini kutmaymiz
      fetch("/api/yangi", { method: "POST", headers: headers(), body: JSON.stringify({ suhbat_id: old }), keepalive: true }).catch(() => {});
    }
    log.querySelectorAll(".msg, .notice").forEach((m) => m.remove());
    $("empty").hidden = false;
    showBanner("");
    input.focus();
  });

  // ---------- agent javobi ko'rinishi ----------
  function botView() {
    const wrap = el("div", "msg bot");
    const card = el("div", "card");
    card.setAttribute("aria-busy", "true");
    const cursor = el("div", "typing");
    card.appendChild(cursor);
    wrap.appendChild(card);
    log.appendChild(wrap);
    return {
      wrap, card, cursor,
      seg: null, // joriy matn bo'lagi: { node, text }
      segments: [], dirty: false, timer: null,
      steps: null, attempt: [], // attempt — joriy API so'rovi davomida qo'shilgan tugunlar
      think: null, thinkMark: 0, thinkSep: false,
      failed: false,
    };
  }
  function add(v, node) {
    v.card.insertBefore(node, v.cursor);
    v.attempt.push(node);
    return node;
  }
  function renderSegment(v) {
    if (v.seg && v.dirty) {
      renderMd(v.seg.node, v.seg.text);
      v.dirty = false;
      scrollDown();
    }
  }
  // Bo'lak tugaganda kutilayotgan chizishni darhol bajaradi — aks holda oxirgi belgilar ko'rinmay qoladi
  function finishSegment(v) {
    if (v.timer) {
      clearTimeout(v.timer);
      v.timer = null;
    }
    renderSegment(v);
  }
  function newSegment(v) {
    finishSegment(v);
    v.seg = { node: add(v, el("div", "md")), text: "" };
    v.segments.push(v.seg);
    v.steps = null;
  }
  function addStep(v, text, cls) {
    finishSegment(v);
    v.seg = null;
    if (!v.steps) v.steps = add(v, el("div", "steps"));
    const s = el("div", "step" + (cls ? " " + cls : ""), text);
    v.steps.appendChild(s);
    scrollDown();
    return s;
  }
  function addNote(v, cls, text) {
    finishSegment(v);
    v.seg = null;
    v.steps = null;
    const n = add(v, el("div", cls, text));
    scrollDown();
    return n;
  }
  function startAttempt(v) {
    finishSegment(v);
    v.attempt = [];
    v.seg = null;
    v.steps = null;
    v.thinkMark = v.think ? v.think.textContent.length : 0;
  }
  // Bekor qilingan urinishda ko'rsatilgan hamma narsani olib tashlaydi (model uni "ko'rmagan")
  function discardAttempt(v) {
    finishSegment(v);
    const gone = new Set(v.attempt);
    v.attempt.forEach((n) => n.remove());
    v.segments = v.segments.filter((s) => !gone.has(s.node));
    v.attempt = [];
    v.seg = null;
    v.steps = null;
    v.dirty = false;
    if (v.think) {
      v.think.textContent = v.think.textContent.slice(0, v.thinkMark);
      if (!v.think.textContent) {
        v.think.parentNode.remove();
        v.think = null;
      }
    }
  }

  function handle(v, ev) {
    switch (ev.t) {
      case "suhbat":
        suhbatId = ev.id;
        if (ev.yoqolgan) {
          log.insertBefore(el("div", "notice", "Serverda bu suhbatning oldingi tarixi topilmadi (server qayta ishga tushgan yoki suhbat muddati o'tgan). Agent yuqoridagi xabarlarni eslamaydi — kerakli ma'lumotni qayta yuboring."), v.wrap);
        }
        break;
      case "qadam": startAttempt(v); break;
      case "blok": newSegment(v); break;
      case "matn":
        if (!v.seg) newSegment(v);
        v.seg.text += ev.v;
        v.dirty = true;
        if (!v.timer) v.timer = setTimeout(() => { v.timer = null; renderSegment(v); }, RENDER_INTERVAL);
        break;
      case "fikr_blok": v.thinkSep = true; break;
      case "fikr":
        if (!v.think) {
          const d = el("details", "think");
          d.appendChild(el("summary", null, "Fikrlash jarayoni"));
          v.think = el("div");
          d.appendChild(v.think);
          v.card.insertBefore(d, v.card.firstChild);
        }
        if (v.thinkSep && v.think.textContent) v.think.textContent += "\n\n";
        v.thinkSep = false;
        v.think.textContent += ev.v;
        break;
      case "vosita": {
        const f = TOOL_LABELS[ev.nom];
        addStep(v, "⚙ " + (f ? f(ev.kirish || {}) : ev.nom));
        break;
      }
      case "natija": if (ev.xato) addStep(v, "⚠ " + ev.qisqa, "err"); break;
      case "qidiruv": addStep(v, `🌐 Internetda qidiruv: "${ev.v}"`); break;
      case "manbalar": {
        const links = (ev.v || []).filter((m) => /^https?:\/\//i.test(m.url)).slice(0, 5);
        if (!links.length) break;
        const s = addStep(v, "Manbalar: ");
        links.forEach((m, i) => {
          const a = el("a", null, m.nom || m.url);
          a.href = m.url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          if (i) s.appendChild(document.createTextNode(" · "));
          s.appendChild(a);
        });
        break;
      }
      case "zaxira": addNote(v, "note", `Javobni zaxira model davom ettirdi (${ev.ga}).`); break;
      case "qayta":
        discardAttempt(v);
        addStep(v, "↻ Model javobi buzilib keldi — qayta so'ralmoqda…");
        break;
      case "bekor": discardAttempt(v); break;
      case "xato":
        addNote(v, "error", ev.v);
        v.failed = true;
        break;
      case "tugadi": {
        const u = ev.usage || {};
        let note = "";
        if (ev.sabab === "max_tokens") note = "Javob uzunlik chegarasida kesildi — \"davom et\" deb yozing. ";
        if (ev.sabab === "qadam_chegarasi") note = "Qadamlar chegarasiga yetildi — \"davom et\" deb yozing. ";
        const total = (u.kirish || 0) + (u.kesh_oqildi || 0) + (u.kesh_yozildi || 0);
        note += `Tokenlar: kirish ${total.toLocaleString("ru-RU")} (keshdan ${(u.kesh_oqildi || 0).toLocaleString("ru-RU")}), chiqish ${(u.chiqish || 0).toLocaleString("ru-RU")}`;
        addNote(v, "note", note);
        const full = v.segments.map((s) => s.text.trim()).filter(Boolean).join("\n\n");
        if (full) {
          const row = el("div", "actions");
          const b = el("button", null, "Javobni nusxalash");
          b.type = "button";
          b.addEventListener("click", () => copyText(full, b));
          row.appendChild(b);
          v.card.insertBefore(row, v.cursor);
        }
        break;
      }
    }
  }

  // ---------- yuborish ----------
  function userMessage(text, imgs) {
    const um = el("div", "msg user");
    const bubble = el("div", "bubble");
    imgs.forEach((im, idx) => {
      const i = el("img");
      i.src = im.url;
      i.alt = `yuborilgan rasm ${idx + 1}`;
      bubble.appendChild(i);
    });
    if (text) bubble.appendChild(document.createTextNode(text));
    um.appendChild(bubble);
    log.appendChild(um);
    return um;
  }

  async function send() {
    const text = input.value.trim();
    if (busy || (!text && !images.length)) return;
    const sent = images;
    $("empty").hidden = true;
    showBanner("");
    const um = userMessage(text, sent);
    const payload = { suhbat_id: suhbatId, matn: text, rasmlar: sent.map(({ media_type, data }) => ({ media_type, data })) };
    input.value = "";
    autosize();
    images = [];
    drawThumbs();
    const v = botView();
    scrollDown(true);
    const gen = generation;
    const ctl = new AbortController();
    controller = ctl;
    setBusy(true);
    let finished = false;
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: headers(), body: JSON.stringify(payload), signal: ctl.signal });
      if (gen !== generation) return;
      if (!r.ok) {
        // Server xabarni qabul qilmadi — yozilgan matn va rasmlar yo'qolmasin
        const detail = await errorDetail(r);
        um.remove();
        v.wrap.remove();
        $("empty").hidden = log.querySelector(".msg") !== null;
        input.value = text;
        autosize();
        images = sent;
        drawThumbs();
        if (r.status === 401) {
          setToken("");
          askPassword("Qaytadan kiring.");
        } else {
          showBanner(detail);
        }
        return;
      }
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        if (gen !== generation) {
          reader.cancel().catch(() => {});
          return;
        }
        buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of chunk.split("\n")) {
            if (!line.startsWith("data:")) continue;
            let ev;
            try { ev = JSON.parse(line.slice(5)); } catch (_) { continue; }
            if (ev.t === "tugadi") finished = true;
            handle(v, ev);
          }
        }
      }
      if (!finished && !v.failed) handle(v, { t: "xato", v: "Javob to'liq kelmadi — bu navbat suhbat tarixiga kirmadi. Qayta yuboring." });
    } catch (err) {
      if (gen !== generation) return;
      if (err && err.name === "AbortError") {
        if (!finished) addNote(v, "note", "To'xtatildi — bu navbat suhbat tarixiga kirmadi.");
      } else {
        handle(v, { t: "xato", v: "Server bilan aloqa uzildi — bu navbat suhbat tarixiga kirmadi." });
      }
    } finally {
      finishSegment(v);
      if (gen === generation) {
        v.cursor.remove();
        v.card.setAttribute("aria-busy", "false");
        if (controller === ctl) {
          controller = null;
          setBusy(false);
        }
        scrollDown();
        input.focus();
      }
    }
  }

  loadStatus();
  input.focus();
})();
