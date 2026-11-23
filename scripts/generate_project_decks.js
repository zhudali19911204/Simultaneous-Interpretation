const fs = require("fs");
const path = require("path");
const PptxGenJS = require("pptxgenjs");

const OUT_DIR = path.resolve(process.cwd(), "project_presentation_versions");
fs.mkdirSync(OUT_DIR, { recursive: true });
const LOGO_PATH = path.join(OUT_DIR, "brand_logo.png");
if (!fs.existsSync(LOGO_PATH)) {
  throw new Error(
    `Missing ${LOGO_PATH}. Run "python scripts/export_presentation_logo.py" first.`,
  );
}

const FONT = "Microsoft YaHei";
const FONT_LATIN = "Arial";
const SW = 13.333;
const SH = 7.5;

const PALETTES = {
  value: {
    orange: "FF6A13",
    orangeDark: "D94D00",
    orangeSoft: "FFF0E8",
    charcoal: "30343B",
    charcoal2: "454A52",
    gray: "69707A",
    mid: "A7ADB5",
    line: "D7DADF",
    light: "F3F4F6",
    paper: "FFFFFF",
    ink: "23262B",
    white: "FFFFFF",
    green: "2F855A",
  },
  tech: {
    orange: "F47A1F",
    orangeDark: "B84900",
    orangeSoft: "3B2A1F",
    charcoal: "1D2025",
    charcoal2: "2B3037",
    gray: "8D949E",
    mid: "B9BEC5",
    line: "464C55",
    light: "E9EBEE",
    paper: "24282E",
    ink: "F7F8FA",
    white: "FFFFFF",
    green: "64B883",
  },
  journey: {
    orange: "E8752A",
    orangeDark: "B95013",
    orangeSoft: "FFF2EA",
    charcoal: "3B3F45",
    charcoal2: "555B63",
    gray: "747B84",
    mid: "B2B7BD",
    line: "D9DDE1",
    light: "F5F6F7",
    paper: "FFFFFF",
    ink: "282B2F",
    white: "FFFFFF",
    green: "3C8C63",
  },
};

function shadow(color = "000000", opacity = 0.14, blur = 1.5, angle = 45, distance = 1.2) {
  return { type: "outer", color, opacity, blur, angle, distance };
}

function noLine() {
  return { color: "FFFFFF", transparency: 100 };
}

function addRect(slide, x, y, w, h, fill, radius = 0.12, line = null, shadowOpt = null) {
  const opts = {
    x, y, w, h,
    rectRadius: radius,
    fill: typeof fill === "string" ? { color: fill } : fill,
    line: line || noLine(),
  };
  if (shadowOpt) opts.shadow = shadowOpt;
  slide.addShape(radius > 0 ? "roundRect" : "rect", opts);
}

function addLine(
  slide,
  x,
  y,
  w,
  h,
  color,
  width = 1.5,
  dash = "solid",
  endArrowType = null,
  beginArrowType = null,
) {
  const opts = { x, y, w, h, line: { color, width, dash } };
  if (endArrowType) opts.line.endArrowType = endArrowType;
  if (beginArrowType) opts.line.beginArrowType = beginArrowType;
  slide.addShape("line", opts);
}

function addText(slide, text, x, y, w, h, options = {}) {
  slide.addText(text, {
    x, y, w, h,
    fontFace: options.fontFace || FONT,
    fontSize: options.fontSize || 16,
    color: options.color || "23262B",
    bold: options.bold || false,
    align: options.align || "left",
    valign: options.valign || "mid",
    margin: options.margin === undefined ? 0 : options.margin,
    breakLine: options.breakLine,
    isTextBox: true,
    fit: options.fit || "shrink",
    paraSpaceAfterPt: options.paraSpaceAfterPt,
    charSpacing: options.charSpacing,
    italic: options.italic,
    transparency: options.transparency,
    rotate: options.rotate,
    bullet: options.bullet,
  });
}

function addRichText(slide, runs, x, y, w, h, options = {}) {
  slide.addText(runs, {
    x, y, w, h,
    fontFace: options.fontFace || FONT,
    fontSize: options.fontSize || 16,
    color: options.color || "23262B",
    align: options.align || "left",
    valign: options.valign || "mid",
    margin: options.margin === undefined ? 0 : options.margin,
    fit: options.fit || "shrink",
  });
}

function addPill(slide, text, x, y, w, h, fill, color, fontSize = 11) {
  addRect(slide, x, y, w, h, fill, h / 2, null, null);
  addText(slide, text, x, y, w, h, {
    fontSize, color, bold: true, align: "center",
  });
}

function addBadge(slide, label, x, y, size, palette, mode = "solid") {
  const fill = mode === "solid" ? palette.orange : palette.orangeSoft;
  const color = mode === "solid" ? palette.white : palette.orangeDark;
  slide.addShape("ellipse", {
    x, y, w: size, h: size,
    fill: { color: fill },
    line: noLine(),
  });
  addText(slide, label, x, y, size, size, {
    fontSize: label.length > 2 ? size * 10 : size * 14,
    color, bold: true, align: "center",
    fontFace: label.match(/^[A-Za-z0-9]+$/) ? FONT_LATIN : FONT,
  });
}

function addLogo(slide, x, y, w, h, dark = "5B5D62", orange = "FF6A13") {
  // The application mark is exported from the project's own Pillow renderer.
  // Use a real PNG because older Office 2016 builds can reject SVG fallback parts.
  slide.addImage({ path: LOGO_PATH, x, y, w, h });
}

function addFooter(slide, palette, versionLabel, page, dark = false) {
  const color = dark ? palette.mid : palette.gray;
  addText(slide, versionLabel, 0.62, 7.08, 3.1, 0.18, {
    fontSize: 8.5, color, charSpacing: 0.5,
  });
  addText(slide, String(page).padStart(2, "0"), 12.2, 7.03, 0.5, 0.25, {
    fontSize: 9.5, color, bold: true, align: "right", fontFace: FONT_LATIN,
  });
}

function addSlideTitle(slide, title, subtitle, palette, dark = false) {
  const ink = dark ? palette.white : palette.ink;
  const muted = dark ? palette.mid : palette.gray;
  addText(slide, title, 0.62, 0.45, 9.7, 0.62, {
    fontSize: 29, color: ink, bold: true,
  });
  if (subtitle) {
    addText(slide, subtitle, 0.64, 1.09, 10.6, 0.34, {
      fontSize: 12.5, color: muted,
    });
  }
}

function addJourneySlideTitle(slide, title, subtitle, palette, dark = false) {
  const ink = dark ? palette.white : palette.ink;
  const muted = dark ? palette.mid : palette.gray;
  addText(slide, title, 0.62, 0.7, 11.85, 0.52, {
    fontSize: 27.5, color: ink, bold: true,
  });
  if (subtitle) {
    addText(slide, subtitle, 0.64, 1.29, 10.7, 0.27, {
      fontSize: 11.5, color: muted,
    });
  }
}

function addSectionLabel(slide, text, x, y, palette, dark = false) {
  addPill(
    slide,
    text,
    x,
    y,
    Math.max(1.1, 0.17 * text.length + 0.38),
    0.32,
    dark ? palette.orangeSoft : palette.orangeSoft,
    dark ? palette.orange : palette.orangeDark,
    9.5,
  );
}

function addCardTitle(slide, title, body, x, y, w, h, palette, badge, dark = false) {
  const fill = dark ? palette.charcoal2 : palette.paper;
  const titleColor = dark ? palette.white : palette.ink;
  const bodyColor = dark ? palette.mid : palette.gray;
  addRect(slide, x, y, w, h, fill, 0.14, {
    color: dark ? palette.line : palette.line,
    width: 0.8,
  }, shadow("000000", dark ? 0.22 : 0.1, 1.2, 45, 0.8));
  addBadge(slide, badge, x + 0.25, y + 0.25, 0.48, palette, dark ? "solid" : "soft");
  addText(slide, title, x + 0.88, y + 0.23, w - 1.12, 0.38, {
    fontSize: 15.5, color: titleColor, bold: true,
  });
  addText(slide, body, x + 0.25, y + 0.68, w - 0.5, Math.max(0.22, h - 0.8), {
    fontSize: 11.5, color: bodyColor, valign: "top",
  });
}

function addBrowserFrame(slide, x, y, w, h, palette, dark = true) {
  const base = dark ? "101820" : palette.paper;
  const top = dark ? "202830" : palette.light;
  addRect(slide, x, y, w, h, base, 0.16, { color: dark ? "343D46" : palette.line, width: 1 },
    shadow("000000", 0.18, 1.8, 45, 1.2));
  addRect(slide, x, y, w, 0.42, top, 0.16, null, null);
  addRect(slide, x, y + 0.28, w, 0.14, top, 0, null, null);
  ["FF6A13", "A7ADB5", "D7DADF"].forEach((c, i) => {
    slide.addShape("ellipse", {
      x: x + 0.22 + i * 0.22, y: y + 0.15, w: 0.09, h: 0.09,
      fill: { color: c }, line: noLine(),
    });
  });
}

function addWaveform(slide, x, y, w, h, palette, points = 22, color = null) {
  const c = color || palette.orange;
  const heights = [0.22, 0.52, 0.85, 0.36, 0.68, 0.92, 0.48, 0.28, 0.74, 0.95, 0.58, 0.34, 0.82, 0.45, 0.62, 0.88, 0.31, 0.73, 0.55, 0.26, 0.66, 0.39];
  const gap = w / points;
  for (let i = 0; i < points; i++) {
    const barH = h * heights[i % heights.length];
    addRect(slide, x + i * gap, y + (h - barH) / 2, gap * 0.45, barH, c, gap * 0.2, null, null);
  }
}

function createDeck(metadata) {
  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE";
  pptx.author = "OpenAI Codex";
  pptx.company = "Simultaneous Interpreter";
  pptx.subject = metadata.subject;
  pptx.title = metadata.title;
  pptx.lang = "zh-CN";
  pptx.theme = {
    headFontFace: FONT,
    bodyFontFace: FONT,
    lang: "zh-CN",
  };
  pptx.defineSlideMaster({
    title: "BLANK",
    background: { color: "FFFFFF" },
    objects: [],
    slideNumber: { x: 12.2, y: 7.03, color: "888888", fontFace: FONT_LATIN, fontSize: 9 },
  });
  return pptx;
}

function addProductUiMockup(slide, x, y, w, h, palette) {
  addBrowserFrame(slide, x, y, w, h, palette, true);
  addText(slide, "Teams 中英同声翻译助手", x + 0.32, y + 0.55, w * 0.56, 0.26, {
    fontSize: 11, color: palette.white, bold: true,
  });
  addPill(slide, "● 准备就绪", x + w - 1.55, y + 0.52, 1.18, 0.28, "26323A", "B9BEC5", 8.2);
  addRect(slide, x + 0.3, y + 0.98, w - 0.6, 0.56, "19242E", 0.09, { color: "343D46", width: 0.6 }, null);
  const labels = ["实体麦克风", "Teams 回放", "英文输出", "英文声音"];
  labels.forEach((label, i) => {
    const itemW = (w - 0.92) / 4;
    addText(slide, label, x + 0.42 + i * itemW, y + 1.08, itemW - 0.1, 0.15, {
      fontSize: 6.8, color: "9DA7B1",
    });
    addRect(slide, x + 0.42 + i * itemW, y + 1.27, itemW - 0.12, 0.15, "27333E", 0.04, null, null);
  });
  addPill(slide, "开始同传", x + 0.32, y + 1.72, 0.95, 0.31, palette.orange, palette.white, 8.2);
  addPill(slide, "显示演示字幕", x + 1.38, y + 1.72, 1.22, 0.31, "27333E", "D6DBE0", 7.8);
  const cardY = y + 2.22;
  const cardW = (w - 0.92) / 2;
  [
    { role: "对方", title: "英文 → 中文字幕", badge: "0C4A6E", sample: "We can confirm the delivery plan today." },
    { role: "我", title: "中文 → 英文语音", badge: "5A3AA8", sample: "我们今天可以确认交付计划。" },
  ].forEach((item, i) => {
    const cx = x + 0.32 + i * (cardW + 0.28);
    addRect(slide, cx, cardY, cardW, h - 2.54, "17212B", 0.11, { color: "303B45", width: 0.7 }, null);
    addPill(slide, item.role, cx + 0.2, cardY + 0.2, 0.48, 0.25, item.badge, "FFFFFF", 7.2);
    addText(slide, item.title, cx + 0.78, cardY + 0.18, cardW - 0.98, 0.28, {
      fontSize: 9, color: "E7EBEF", bold: true,
    });
    addText(slide, item.sample, cx + 0.2, cardY + 0.7, cardW - 0.4, 0.62, {
      fontSize: 10, color: i === 0 ? "7DD3FC" : "C4B5FD", valign: "top",
    });
    addText(slide, i === 0 ? "最终译文持续记录" : "英文文本 + 可选合成语音", cx + 0.2, cardY + 1.43, cardW - 0.4, 0.35, {
      fontSize: 7.5, color: "8E99A4",
    });
  });
}

function buildValueDeck() {
  const p = PALETTES.value;
  const pptx = createDeck({
    title: "Teams 中英同声翻译助手｜产品价值版",
    subject: "项目介绍：产品价值、核心体验与落地能力",
  });

  // Slide 1
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.charcoal };
    addLogo(slide, 0.68, 0.58, 0.58, 0.66, "F2F3F5", p.orange);
    addPill(slide, "版本 A · 产品价值", 10.8, 0.58, 1.78, 0.36, "464B53", "F3F4F6", 9.5);
    addText(slide, "让跨语言会议", 0.72, 1.5, 6.25, 0.78, {
      fontSize: 36, color: p.white, bold: true,
    });
    addText(slide, "听得懂、跟得上、留得住", 0.72, 2.26, 7.25, 0.9, {
      fontSize: 40, color: p.orange, bold: true,
    });
    addText(slide, "Teams 中英同声翻译助手", 0.75, 3.38, 5.7, 0.38, {
      fontSize: 19, color: "E7E9EC", bold: true,
    });
    addText(slide, "Windows 本地桌面助手，将双向同传、会议理解与会后纪要连接为一个工作闭环。", 0.75, 3.9, 5.9, 0.82, {
      fontSize: 14, color: p.mid, valign: "top",
    });
    ["双向实时同传", "会中智能理解", "会后结构化沉淀"].forEach((t, i) => {
      addPill(slide, t, 0.75 + i * 1.72, 5.08, 1.55, 0.42, i === 0 ? p.orange : "484D55", p.white, 10);
    });

    addRect(slide, 7.55, 1.25, 4.95, 4.95, "272B31", 0.28, { color: "4B5058", width: 1 }, shadow("000000", 0.28, 2.2, 45, 1.5));
    slide.addShape("ellipse", {
      x: 9.15, y: 2.2, w: 1.8, h: 1.8,
      fill: { color: p.orange, transparency: 8 },
      line: noLine(),
    });
    addText(slide, "会议", 9.15, 2.2, 1.8, 1.8, {
      fontSize: 25, color: p.white, bold: true, align: "center",
    });
    addWaveform(slide, 8.12, 1.65, 3.85, 0.45, p, 22, "FF9A5C");
    addRect(slide, 8.02, 4.28, 1.9, 0.83, "3A3F46", 0.18, null, null);
    addText(slide, "EN  →  中", 8.02, 4.28, 1.9, 0.83, {
      fontSize: 18, color: p.white, bold: true, align: "center", fontFace: FONT_LATIN,
    });
    addRect(slide, 10.15, 4.28, 1.9, 0.83, p.orange, 0.18, null, null);
    addText(slide, "中  →  EN", 10.15, 4.28, 1.9, 0.83, {
      fontSize: 18, color: p.white, bold: true, align: "center", fontFace: FONT_LATIN,
    });
    addText(slide, "字幕 · 语音 · 画面 · 纪要", 8.22, 5.52, 3.65, 0.32, {
      fontSize: 11, color: p.mid, align: "center",
    });
    addFooter(slide, p, "TEAMS INTERPRETER / PRODUCT VALUE", 1, true);
    slide.addNotes("一句话定位：这是面向中英 Teams 会议的本地智能助手，目标是让参会者不仅听懂，还能理解并沉淀会议。");
  }

  // Slide 2
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.light };
    addSlideTitle(slide, "从“翻一句话”到“闭环一场会”", "项目围绕会议中的三个断点，构建连续体验。", p, false);
    addSectionLabel(slide, "会议断点", 0.65, 1.56, p, false);
    const issues = [
      ["语言不同步", "听懂需要等待或二次确认，讨论节奏被打断。", "01"],
      ["信息难跟踪", "字幕、共享画面、结论分散，关键上下文容易丢失。", "02"],
      ["结果难沉淀", "会后再整理耗时，行动项和未决问题容易遗漏。", "03"],
    ];
    issues.forEach((d, i) => {
      addCardTitle(slide, d[0], d[1], 0.65, 2.02 + i * 1.38, 4.55, 1.12, p, d[2], false);
    });

    addSectionLabel(slide, "产品闭环", 6.0, 1.56, p, false);
    addRect(slide, 6.0, 2.02, 6.7, 4.15, p.paper, 0.2, { color: p.line, width: 1 }, shadow("000000", 0.08, 1.2, 45, 0.8));
    const nodes = [
      { t: "听懂", d: "中英双向字幕", x: 6.55, y: 2.5 },
      { t: "参与", d: "英文合成语音", x: 9.95, y: 2.5 },
      { t: "理解", d: "问答与画面速读", x: 9.95, y: 4.65 },
      { t: "沉淀", d: "纪要与关键截图", x: 6.55, y: 4.65 },
    ];
    nodes.forEach((n, i) => {
      slide.addShape("ellipse", {
        x: n.x, y: n.y, w: 1.5, h: 1.05,
        fill: { color: i % 2 === 0 ? p.orange : p.charcoal },
        line: noLine(),
      });
      addText(slide, n.t, n.x, n.y + 0.03, 1.5, 0.46, {
        fontSize: 18, color: p.white, bold: true, align: "center",
      });
      addText(slide, n.d, n.x - 0.22, n.y + 1.13, 1.94, 0.35, {
        fontSize: 9.5, color: p.gray, align: "center",
      });
    });
    addLine(slide, 8.13, 3.03, 1.7, 0, p.orange, 2, "dash", "triangle");
    addLine(slide, 10.7, 3.68, 0, 0.83, p.orange, 2, "dash", "triangle");
    addLine(slide, 8.18, 5.17, 1.6, 0, p.orange, 2, "dash", null, "triangle");
    addLine(slide, 7.3, 3.72, 0, 0.78, p.orange, 2, "dash", null, "triangle");
    addText(slide, "一个入口\n贯穿会前 · 会中 · 会后", 8.22, 3.55, 2.12, 1.22, {
      fontSize: 15, color: p.ink, bold: true, align: "center",
    });
    addFooter(slide, p, "TEAMS INTERPRETER / PRODUCT VALUE", 2, false);
    slide.addNotes("这里强调产品不是单点翻译，而是围绕会议全流程形成听懂、参与、理解、沉淀四个环节。");
  }

  // Slide 3
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.paper };
    addSlideTitle(slide, "核心体验：双向同传不中断会议节奏", "对方说英文，你看中文；你说中文，对方可听到英文。", p, false);
    addProductUiMockup(slide, 0.65, 1.68, 7.55, 4.86, p);

    addCardTitle(slide, "英 → 中", "捕获 Teams 回放声音，实时显示中文字幕；默认不生成中文语音，控制不必要的成本。", 8.55, 1.72, 4.12, 1.35, p, "EN", false);
    addCardTitle(slide, "中 → 英", "采集本地麦克风，输出英文译文；需要时通过虚拟声卡把合成英语送回 Teams。", 8.55, 3.27, 4.12, 1.35, p, "中", false);
    addCardTitle(slide, "演示字幕", "独立置顶双语字幕条，可拖动到其他屏幕，适配 PowerPoint 放映与全屏共享。", 8.55, 4.82, 4.12, 1.35, p, "PPT", false);
    addFooter(slide, p, "TEAMS INTERPRETER / PRODUCT VALUE", 3, false);
    slide.addNotes("核心使用路径：选择麦克风、Teams 回放和英文输出后开始同传。英文来声显示中文；中文发言可输出英文文本与语音。");
  }

  // Slide 4
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.charcoal };
    addSlideTitle(slide, "AI 会议理解：会中有依据，会后可追溯", "字幕时间线与共享画面在同一条会议上下文中关联。", p, true);
    const steps = [
      { n: "01", title: "完整时间线", body: "按“我 / 对方”查看最终字幕，支持筛选、搜索与复制。", badge: "时" },
      { n: "02", title: "有依据的问答", body: "回答必须附时间与角色证据；没有可核验依据时拒绝猜测。", badge: "问" },
      { n: "03", title: "共享画面理解", body: "主动框选 Teams / PPT 区域，稳定换页后生成中文速读。", badge: "图" },
      { n: "04", title: "结构化纪要", body: "汇总摘要、结论、行动项、风险，并选择关键页面截图。", badge: "记" },
    ];
    steps.forEach((s, i) => {
      const x = 0.68 + i * 3.08;
      addText(slide, s.n, x, 1.66, 0.65, 0.3, {
        fontSize: 10.5, color: p.orange, bold: true, fontFace: FONT_LATIN,
      });
      addRect(slide, x, 2.05, 2.78, 3.45, p.charcoal2, 0.18, { color: "4E545C", width: 0.8 }, shadow("000000", 0.2, 1.3, 45, 1));
      addBadge(slide, s.badge, x + 0.28, 2.34, 0.62, p, "solid");
      addText(slide, s.title, x + 0.28, 3.2, 2.18, 0.42, {
        fontSize: 16, color: p.white, bold: true,
      });
      addText(slide, s.body, x + 0.28, 3.82, 2.18, 1.2, {
        fontSize: 11.5, color: p.mid, valign: "top",
      });
      if (i < steps.length - 1) {
        addLine(slide, x + 2.78, 3.73, 0.3, 0, p.orange, 2, "solid", "triangle");
      }
    });
    addRect(slide, 3.38, 5.9, 6.65, 0.58, { color: "3A3F46", transparency: 0 }, 0.16, null, null);
    addText(slide, "统一会议上下文：字幕 + 时间 + 角色 + 页面", 3.38, 5.9, 6.65, 0.58, {
      fontSize: 13.5, color: p.white, bold: true, align: "center",
    });
    addFooter(slide, p, "TEAMS INTERPRETER / PRODUCT VALUE", 4, true);
    slide.addNotes("四个能力共享同一份会议上下文。会中问答强调证据，视觉页面与字幕时间线关联，最后共同进入会议纪要。");
  }

  // Slide 5
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.light };
    addSlideTitle(slide, "可落地的 Windows 本地助手", "兼顾可配置性、隐私边界与工程可靠性。", p, false);
    const pillars = [
      {
        badge: "隐",
        title: "隐私默认收敛",
        lines: ["音频默认不录制、不落盘", "API Key 进入 Windows 凭据管理器", "日志不记录凭据、音频、字幕或会议内容"],
      },
      {
        badge: "配",
        title: "多供应商可配置",
        lines: ["同传与会议智能可使用不同 Key", "8 类纪要 / 视觉供应商预设", "支持自定义兼容接口与本地 Ollama"],
      },
      {
        badge: "稳",
        title: "工程化交付",
        lines: ["116 个自动化测试用例", "双方向独立重连与脱敏诊断", "Windows x64 安装包与免安装版本"],
      },
    ];
    pillars.forEach((item, i) => {
      const x = 0.68 + i * 4.18;
      addRect(slide, x, 1.72, 3.78, 3.86, p.paper, 0.2, { color: p.line, width: 0.8 }, shadow("000000", 0.09, 1.3, 45, 0.8));
      addBadge(slide, item.badge, x + 0.35, 2.05, 0.7, p, i === 1 ? "solid" : "soft");
      addText(slide, item.title, x + 0.35, 2.95, 3.05, 0.42, {
        fontSize: 17, color: p.ink, bold: true,
      });
      item.lines.forEach((line, j) => {
        slide.addShape("ellipse", {
          x: x + 0.38, y: 3.62 + j * 0.57, w: 0.1, h: 0.1,
          fill: { color: p.orange }, line: noLine(),
        });
        addText(slide, line, x + 0.62, 3.48 + j * 0.57, 2.77, 0.36, {
          fontSize: 10.5, color: p.gray,
        });
      });
    });
    addRect(slide, 2.16, 6.0, 9.0, 0.58, p.charcoal, 0.18, null, null);
    addRichText(slide, [
      { text: "选择供应商", options: { bold: true, color: p.white } },
      { text: "  ·  完成音频路由  ·  ", options: { color: p.mid } },
      { text: "开始同传", options: { bold: true, color: p.orange } },
    ], 2.16, 6.0, 9.0, 0.58, {
      fontSize: 14, align: "center", color: p.white,
    });
    addFooter(slide, p, "TEAMS INTERPRETER / PRODUCT VALUE", 5, false);
    slide.addNotes("落地基础包括凭据安全、多供应商配置、自动化测试、诊断与 Windows 发布流程。");
  }

  return { pptx, file: path.join(OUT_DIR, "版本A_产品价值型_项目介绍.pptx") };
}

function addArchitectureBlock(slide, x, y, w, h, title, body, palette, accent = false) {
  const fill = accent ? palette.orange : palette.charcoal2;
  const titleColor = palette.white;
  const bodyColor = accent ? "FFF4EC" : palette.mid;
  addRect(slide, x, y, w, h, fill, 0.13, { color: accent ? palette.orange : palette.line, width: 0.8 },
    shadow("000000", 0.22, 1.2, 45, 0.8));
  addText(slide, title, x + 0.22, y + 0.18, w - 0.44, 0.34, {
    fontSize: 14, color: titleColor, bold: true,
  });
  addText(slide, body, x + 0.22, y + 0.63, w - 0.44, h - 0.78, {
    fontSize: 9.8, color: bodyColor, valign: "top",
  });
}

function buildTechDeck() {
  const p = PALETTES.tech;
  const pptx = createDeck({
    title: "Teams 中英同声翻译助手｜技术架构版",
    subject: "项目介绍：本地编排、实时链路、会议智能与工程能力",
  });

  // Slide 1
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.charcoal };
    addLogo(slide, 0.68, 0.55, 0.55, 0.64, "E7E9EC", p.orange);
    addPill(slide, "版本 B · 技术架构", 10.64, 0.57, 1.95, 0.36, p.charcoal2, p.light, 9.5);
    addText(slide, "从音频到行动项", 0.72, 1.53, 5.8, 0.75, {
      fontSize: 38, color: p.white, bold: true,
    });
    addText(slide, "一条本地编排链", 0.72, 2.33, 5.8, 0.75, {
      fontSize: 40, color: p.orange, bold: true,
    });
    addText(slide, "Teams 中英同声翻译助手", 0.75, 3.42, 5.15, 0.38, {
      fontSize: 18.5, color: p.light, bold: true,
    });
    addText(slide, "Windows / WASAPI / WebSocket / 多模型适配 / 会议上下文", 0.75, 3.95, 5.95, 0.4, {
      fontSize: 12.5, color: p.mid, fontFace: FONT_LATIN,
    });

    const lanes = [
      ["音频接入", "WASAPI\n物理设备 + 回放捕获"],
      ["实时服务", "双向 WebSocket\n翻译 + TTS"],
      ["会议智能", "字幕时间线\n问答 + 视觉 + 纪要"],
      ["交付运维", "凭据 / 日志\n测试 + Windows 发布"],
    ];
    lanes.forEach((lane, i) => {
      const x = 6.72 + (i % 2) * 2.85;
      const y = 1.47 + Math.floor(i / 2) * 2.17;
      addArchitectureBlock(slide, x, y, 2.45, 1.62, lane[0], lane[1], p, i === 1);
      addText(slide, String(i + 1).padStart(2, "0"), x + 1.72, y + 0.15, 0.48, 0.28, {
        fontSize: 9, color: i === 1 ? "FFE0CA" : p.gray, bold: true, align: "right", fontFace: FONT_LATIN,
      });
    });
    addLine(slide, 7.96, 3.24, 3.32, 0, p.orange, 1.6, "dash", null);
    addLine(slide, 9.59, 2.33, 0, 2.24, p.orange, 1.6, "dash", null);
    addRect(slide, 6.82, 5.88, 5.05, 0.52, "343940", 0.15, null, null);
    addText(slide, "本地负责采集、路由、编排与呈现；模型服务按配置接入", 6.82, 5.88, 5.05, 0.52, {
      fontSize: 11, color: p.light, bold: true, align: "center",
    });
    addFooter(slide, p, "TEAMS INTERPRETER / TECH ARCHITECTURE", 1, true);
    slide.addNotes("技术定位：本地桌面端承担音频采集、链路编排、状态管理和 UI；实时翻译及大模型能力通过可配置服务接入。");
  }

  // Slide 2
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.paper };
    addSlideTitle(slide, "系统架构：实时链路与会议智能并行", "主链路保证同传体验，旁路沉淀会议上下文。", p, true);
    const layers = [
      {
        label: "设备层", color: "333840",
        blocks: [
          ["物理麦克风", "本地中文发言"],
          ["Teams 回放捕获", "对方英文来声"],
          ["VB-CABLE / 实体设备", "英文语音输出"],
        ],
      },
      {
        label: "实时层", color: "3B2A1F",
        blocks: [
          ["中 → 英会话", "文本 + 可选 TTS"],
          ["英 → 中会话", "中文字幕"],
          ["连接协调器", "独立重连 / 状态事件"],
        ],
      },
      {
        label: "上下文层", color: "333840",
        blocks: [
          ["双语字幕时间线", "角色 + 时间 + 最终译文"],
          ["共享页面时间线", "缩略图 + 摘要 + 关键数字"],
          ["Token / 诊断状态", "用量与脱敏日志"],
        ],
      },
      {
        label: "应用层", color: "3B2A1F",
        blocks: [
          ["演示字幕", "置顶中英双语"],
          ["会议助手", "检索问答 + 实时重点"],
          ["AI 会议纪要", "摘要 + 行动项 + 关键截图"],
        ],
      },
    ];
    layers.forEach((layer, row) => {
      const y = 1.6 + row * 1.25;
      addRect(slide, 0.7, y, 1.25, 0.92, row % 2 === 0 ? p.charcoal2 : p.orange, 0.12, null, null);
      addText(slide, layer.label, 0.7, y, 1.25, 0.92, {
        fontSize: 14, color: p.white, bold: true, align: "center",
      });
      layer.blocks.forEach((b, col) => {
        const x = 2.18 + col * 3.42;
        addRect(slide, x, y, 3.08, 0.92, layer.color, 0.12, { color: p.line, width: 0.7 }, null);
        addText(slide, b[0], x + 0.2, y + 0.13, 2.67, 0.27, {
          fontSize: 12.2, color: p.white, bold: true,
        });
        addText(slide, b[1], x + 0.2, y + 0.5, 2.67, 0.22, {
          fontSize: 8.7, color: p.mid,
        });
      });
      if (row < layers.length - 1) {
        [3.72, 7.14, 10.56].forEach((cx) => {
          addLine(slide, cx, y + 0.93, 0, 0.31, p.orange, 1.5, "solid", "triangle");
        });
      }
    });
    addPill(slide, "本地 Windows 边界", 10.18, 6.38, 2.16, 0.32, p.orangeSoft, p.orange, 8.8);
    addFooter(slide, p, "TEAMS INTERPRETER / TECH ARCHITECTURE", 2, true);
    slide.addNotes("架构分为设备、实时、上下文、应用四层。实时同传主链路与会议智能旁路并行，确保智能功能失败时不影响同传运行。");
  }

  // Slide 3
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.charcoal };
    addSlideTitle(slide, "实时与韧性：为会议连续性设计", "双方向独立运行，临时抖动不清空已经完成的会议记录。", p, true);
    addSectionLabel(slide, "实时音频", 0.68, 1.52, p, true);
    addRect(slide, 0.68, 1.97, 5.58, 2.1, p.charcoal2, 0.16, { color: p.line, width: 0.8 }, null);
    addWaveform(slide, 1.05, 2.4, 4.75, 0.78, p, 22, p.orange);
    const metrics = [
      ["≈20 ms", "本地音频缓冲"],
      ["16 kHz", "输入 PCM"],
      ["24 kHz", "英文语音输出"],
    ];
    metrics.forEach((m, i) => {
      const x = 0.85 + i * 1.78;
      addText(slide, m[0], x, 3.35, 1.55, 0.35, {
        fontSize: 17, color: p.white, bold: true, align: "center", fontFace: FONT_LATIN,
      });
      addText(slide, m[1], x, 3.72, 1.55, 0.22, {
        fontSize: 8.5, color: p.gray, align: "center",
      });
    });
    addSectionLabel(slide, "省流量静音门控", 0.68, 4.38, p, true);
    const gate = [
      ["0.16 s", "保留前导"],
      ["检测说话", "开始上传"],
      ["≈1 s", "保留尾静音"],
      ["长静音", "停止上传"],
    ];
    gate.forEach((g, i) => {
      const x = 0.7 + i * 1.43;
      addPill(slide, g[0], x, 4.87, 1.1, 0.38, i === 1 ? p.orange : p.charcoal2, p.white, 9);
      addText(slide, g[1], x, 5.32, 1.1, 0.26, {
        fontSize: 8.6, color: p.mid, align: "center",
      });
      if (i < gate.length - 1) addLine(slide, x + 1.1, 5.06, 0.31, 0, p.orange, 1.4, "solid", "triangle");
    });

    addSectionLabel(slide, "连接恢复", 6.72, 1.52, p, true);
    addRect(slide, 6.72, 1.97, 5.93, 3.62, p.charcoal2, 0.16, { color: p.line, width: 0.8 }, null);
    addText(slide, "中 → 英", 7.08, 2.26, 1.0, 0.35, {
      fontSize: 12.5, color: p.white, bold: true,
    });
    addText(slide, "英 → 中", 7.08, 3.16, 1.0, 0.35, {
      fontSize: 12.5, color: p.white, bold: true,
    });
    addLine(slide, 8.16, 2.43, 3.85, 0, p.orange, 5, "solid", null);
    addLine(slide, 8.16, 3.33, 1.22, 0, p.green, 5, "solid", null);
    addLine(slide, 9.38, 3.33, 0.82, 0, "6B727C", 5, "dash", null);
    addLine(slide, 10.2, 3.33, 1.81, 0, p.green, 5, "solid", null);
    addPill(slide, "运行", 11.88, 2.23, 0.5, 0.36, p.orange, p.white, 8.5);
    addPill(slide, "恢复", 11.88, 3.13, 0.5, 0.36, p.green, p.white, 8.5);
    addText(slide, "单方向断线时，另一方向继续工作", 7.08, 3.92, 4.95, 0.32, {
      fontSize: 11.5, color: p.light, bold: true,
    });
    addText(slide, "重试节奏", 7.08, 4.48, 1.0, 0.28, {
      fontSize: 9.5, color: p.gray,
    });
    ["2", "5", "10", "20", "30", "30…"].forEach((t, i) => {
      addPill(slide, `${t}s`, 8.15 + i * 0.68, 4.39, 0.54, 0.38, i < 5 ? "3A4048" : p.orange, p.white, 8.2);
    });
    addText(slide, "心跳超时 20 s · 两方向共享连接频率限制 · 非重试错误立即提示", 6.82, 5.91, 5.67, 0.46, {
      fontSize: 10.3, color: p.mid, align: "center",
    });
    addFooter(slide, p, "TEAMS INTERPRETER / TECH ARCHITECTURE", 3, true);
    slide.addNotes("实时链路包含约 20ms 本地缓冲、静音门控、双向独立恢复、递增重试和 20 秒心跳超时。");
  }

  // Slide 4
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.paper };
    addSlideTitle(slide, "会议智能层：上下文统一，模型可替换", "字幕、共享页面与模型输出保持时间关联。", p, true);
    const colX = [0.72, 4.72, 8.72];
    const headers = [
      ["输入证据", p.charcoal2],
      ["会议上下文", p.orange],
      ["智能输出", p.charcoal2],
    ];
    headers.forEach((h, i) => {
      addRect(slide, colX[i], 1.58, 3.55, 0.58, h[1], 0.12, null, null);
      addText(slide, h[0], colX[i], 1.58, 3.55, 0.58, {
        fontSize: 14, color: p.white, bold: true, align: "center",
      });
    });
    const left = [
      ["双语最终字幕", "角色 / 时间 / 源文 / 译文"],
      ["共享画面页面", "标题 / 摘要 / 数字 / 术语"],
      ["用户问题", "当前问题与历史相关片段"],
    ];
    const middle = [
      ["时间线配对", "把同一轮双语内容对齐"],
      ["页面同步", "字幕关联到对应共享页面"],
      ["有界检索", "最近 15 分钟 + 相关历史"],
    ];
    const right = [
      ["会中问答", "附 [时间 角色] 证据"],
      ["实时重点", "议题 / 结论 / 行动项 / 风险"],
      ["会议纪要", "摘要 + 关键截图 + Token"],
    ];
    [left, middle, right].forEach((arr, c) => {
      arr.forEach((b, r) => {
        const x = colX[c];
        const y = 2.48 + r * 1.12;
        addRect(slide, x, y, 3.55, 0.82, c === 1 ? "3B2A1F" : p.charcoal2, 0.11, { color: p.line, width: 0.7 }, null);
        addText(slide, b[0], x + 0.22, y + 0.12, 3.08, 0.26, {
          fontSize: 11.8, color: p.white, bold: true,
        });
        addText(slide, b[1], x + 0.22, y + 0.46, 3.08, 0.2, {
          fontSize: 8.5, color: p.mid,
        });
      });
    });
    [2.83, 3.95, 5.07].forEach((cy) => {
      addLine(slide, 4.28, cy, 0.38, 0, p.orange, 1.8, "solid", "triangle");
      addLine(slide, 8.28, cy, 0.38, 0, p.orange, 1.8, "solid", "triangle");
    });
    addRect(slide, 1.6, 6.14, 10.15, 0.46, "32373E", 0.13, null, null);
    addText(slide, "供应商适配：百炼 · DeepSeek · Kimi · 智谱 · 硅基流动 · OpenAI · Ollama · 自定义兼容服务", 1.6, 6.14, 10.15, 0.46, {
      fontSize: 10.2, color: p.light, bold: true, align: "center",
    });
    addFooter(slide, p, "TEAMS INTERPRETER / TECH ARCHITECTURE", 4, true);
    slide.addNotes("会议智能以时间线为核心，统一字幕和共享页面证据。纪要与视觉能力通过 OpenAI 兼容接口及百炼 Workspace 接入多家模型。");
  }

  // Slide 5
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.charcoal };
    addSlideTitle(slide, "工程就绪：安全、测试、诊断、发布", "从源码到 Windows x64 发布包，形成可验证交付链。", p, true);
    const stats = [
      ["116", "自动化测试用例", "覆盖实时协议、纪要、视觉、设置、主题与连接恢复"],
      ["3", "类模型配置", "同传 / 纪要 / 共享画面，可分别选择供应商与 Key"],
      ["0", "默认音频落盘", "音频与字幕仅按运行需要处理，截图仅在主动导出时保存"],
    ];
    stats.forEach((s, i) => {
      const x = 0.72 + i * 4.15;
      addRect(slide, x, 1.65, 3.72, 1.48, i === 1 ? p.orange : p.charcoal2, 0.16, { color: i === 1 ? p.orange : p.line, width: 0.8 }, shadow("000000", 0.2, 1.2, 45, 0.8));
      addText(slide, s[0], x + 0.28, 1.88, 1.18, 0.56, {
        fontSize: 32, color: p.white, bold: true, fontFace: FONT_LATIN,
      });
      addText(slide, s[1], x + 1.46, 1.86, 1.95, 0.34, {
        fontSize: 12.5, color: p.white, bold: true,
      });
      addText(slide, s[2], x + 1.46, 2.25, 1.95, 0.56, {
        fontSize: 8.5, color: i === 1 ? "FFF0E5" : p.mid, valign: "top",
      });
    });

    const items = [
      ["Windows 凭据管理器", "API Key 不写入项目文件；非敏感设置保存在用户应用目录。", "KEY"],
      ["脱敏滚动日志", "连接日志限制大小并保留 3 份，不记录会议内容与凭据。", "LOG"],
      ["Windows 发布链", "PyInstaller 构建安装包、免安装程序、构建信息与 SHA256。", "ZIP"],
      ["模块化边界", "音频、后端、会议助手、视觉、配置与 UI 可独立测试。", "MOD"],
    ];
    items.forEach((it, i) => {
      const x = 0.72 + (i % 2) * 6.05;
      const y = 3.55 + Math.floor(i / 2) * 1.18;
      addRect(slide, x, y, 5.68, 0.94, p.charcoal2, 0.13, { color: p.line, width: 0.7 }, null);
      addBadge(slide, it[2], x + 0.23, y + 0.22, 0.5, p, i === 2 ? "solid" : "soft");
      addText(slide, it[0], x + 0.93, y + 0.12, 1.78, 0.3, {
        fontSize: 11.8, color: p.white, bold: true,
      });
      addText(slide, it[1], x + 2.7, y + 0.12, 2.65, 0.58, {
        fontSize: 8.8, color: p.mid, valign: "top",
      });
    });
    addPill(slide, "技术定位：可配置、可恢复、可验证的本地会议智能中枢", 3.1, 6.1, 7.15, 0.48, p.orange, p.white, 12);
    addFooter(slide, p, "TEAMS INTERPRETER / TECH ARCHITECTURE", 5, true);
    slide.addNotes("最后总结项目的工程成熟度：凭据隔离、脱敏日志、116 个自动化测试、模块化设计和 Windows 发布流程。");
  }

  return { pptx, file: path.join(OUT_DIR, "版本B_技术架构型_项目介绍.pptx") };
}

function addJourneyStep(slide, n, title, x, y, palette, active = false) {
  addBadge(slide, String(n), x, y, 0.3, palette, active ? "solid" : "soft");
  addText(slide, title, x + 0.4, y - 0.01, 1.15, 0.3, {
    fontSize: 8.4, color: active ? palette.orangeDark : palette.gray, bold: true,
  });
}

function addJourneyHeader(slide, active, palette, dark = false) {
  const steps = ["准备", "连接", "同传", "理解", "沉淀"];
  steps.forEach((t, i) => {
    addJourneyStep(slide, i + 1, t, 0.72 + i * 2.43, 0.2, palette, i + 1 === active);
    if (i < steps.length - 1) {
      addLine(slide, 2.25 + i * 2.43, 0.35, 0.66, 0, dark ? palette.line : palette.line, 1, "dash", null);
    }
  });
}

function buildJourneyDeck() {
  const p = PALETTES.journey;
  const pptx = createDeck({
    title: "Teams 中英同声翻译助手｜会议旅程版",
    subject: "项目介绍：以一场中英会议为主线的场景演示",
  });

  // Slide 1
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.paper };
    addLogo(slide, 0.7, 0.55, 0.52, 0.6, "555B63", p.orange);
    addPill(slide, "版本 C · 会议旅程", 10.66, 0.58, 1.93, 0.36, p.light, p.charcoal, 9.5);
    addText(slide, "一场中英会议的", 0.72, 1.45, 5.6, 0.7, {
      fontSize: 36, color: p.ink, bold: true,
    });
    addText(slide, "五步智能闭环", 0.72, 2.18, 5.6, 0.8, {
      fontSize: 43, color: p.orange, bold: true,
    });
    addText(slide, "从设备准备到会后纪要，全部在一个 Windows 桌面助手中完成。", 0.75, 3.25, 5.4, 0.7, {
      fontSize: 14.5, color: p.gray, valign: "top",
    });
    const steps = [
      ["1", "准备", "设备与模型"],
      ["2", "连接", "开始同传"],
      ["3", "同传", "字幕与语音"],
      ["4", "理解", "问答与画面"],
      ["5", "沉淀", "纪要与截图"],
    ];
    steps.forEach((s, i) => {
      const x = 0.75 + i * 2.35;
      const y = 5.12 + (i % 2) * 0.36;
      addRect(slide, x, y, 1.95, 0.88, i === 2 ? p.orange : p.light, 0.18, { color: i === 2 ? p.orange : p.line, width: 0.8 }, null);
      addText(slide, s[0], x + 0.18, y + 0.15, 0.32, 0.32, {
        fontSize: 15, color: i === 2 ? p.white : p.orange, bold: true, align: "center", fontFace: FONT_LATIN,
      });
      addText(slide, s[1], x + 0.62, y + 0.12, 0.96, 0.3, {
        fontSize: 13, color: i === 2 ? p.white : p.ink, bold: true,
      });
      addText(slide, s[2], x + 0.62, y + 0.47, 1.12, 0.22, {
        fontSize: 8.5, color: i === 2 ? "FFF2E9" : p.gray,
      });
    });
    addFooter(slide, p, "TEAMS INTERPRETER / MEETING JOURNEY", 1, false);
    slide.addNotes("这一版以一场中英会议为主线，按准备、连接、同传、理解和沉淀五个阶段介绍产品。");
  }

  // Slide 2
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.light };
    addJourneyHeader(slide, 1, p, false);
    addJourneySlideTitle(slide, "会前 2 分钟：选好设备与模型", "项目把复杂配置收敛为三个明确决策。", p, false);
    const choices = [
      ["你的声音从哪里来？", "选择实体麦克风", "MIC"],
      ["对方声音从哪里来？", "选择 Teams 扬声器回放捕获", "IN"],
      ["英文声音送到哪里？", "关闭语音，或选择 VB-CABLE / 实体设备", "OUT"],
    ];
    choices.forEach((c, i) => {
      addCardTitle(slide, c[0], c[1], 0.72, 1.72 + i * 1.2, 5.0, 0.94, p, c[2], false);
    });
    addBrowserFrame(slide, 6.18, 1.72, 6.4, 4.52, p, false);
    addText(slide, "模型供应商设置", 6.48, 2.27, 2.6, 0.33, {
      fontSize: 15, color: p.ink, bold: true,
    });
    ["同声翻译", "AI 会议纪要", "共享画面 AI"].forEach((t, i) => {
      addPill(slide, t, 6.48 + i * 1.63, 2.76, 1.43, 0.34, i === 0 ? p.orange : p.light, i === 0 ? p.white : p.gray, 8.8);
    });
    const rows = [
      ["供应商", "阿里云百炼 LiveTranslate"],
      ["实时模型", "qwen3.5-livetranslate-…"],
      ["API Key", "••••••••••••••••"],
      ["WorkspaceId", "华北 2（北京）"],
    ];
    rows.forEach((r, i) => {
      const y = 3.34 + i * 0.58;
      addText(slide, r[0], 6.5, y, 1.15, 0.27, {
        fontSize: 9, color: p.gray,
      });
      addRect(slide, 7.72, y - 0.04, 4.3, 0.4, p.paper, 0.07, { color: p.line, width: 0.7 }, null);
      addText(slide, r[1], 7.94, y - 0.01, 3.85, 0.28, {
        fontSize: 9.2, color: p.ink, fontFace: i === 1 ? FONT_LATIN : FONT,
      });
    });
    addPill(slide, "保存配置", 10.47, 5.78, 1.55, 0.4, p.orange, p.white, 10);
    addPill(slide, "API Key → Windows 凭据管理器", 7.0, 6.42, 3.2, 0.36, p.charcoal, p.white, 9.2);
    addFooter(slide, p, "TEAMS INTERPRETER / MEETING JOURNEY", 2, false);
    slide.addNotes("会前准备包括三个音频设备决策，以及同传、纪要、共享画面三类模型配置。Key 保存到 Windows 凭据管理器。");
  }

  // Slide 3
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.paper };
    addJourneyHeader(slide, 3, p, false);
    addJourneySlideTitle(slide, "会中：双方各说母语，会议自然继续", "字幕负责“看懂”，可选英文语音负责“说出去”。", p, false);
    addProductUiMockup(slide, 0.72, 1.66, 7.5, 4.9, p);

    addText(slide, "对方说英文", 8.68, 1.85, 1.95, 0.35, {
      fontSize: 13.5, color: p.ink, bold: true,
    });
    addWaveform(slide, 8.68, 2.28, 3.35, 0.5, p, 18, p.charcoal2);
    addLine(slide, 10.3, 2.84, 0, 0.42, p.orange, 1.8, "solid", "triangle");
    addRect(slide, 8.68, 3.32, 3.35, 0.76, p.orangeSoft, 0.14, null, null);
    addText(slide, "你看到中文字幕", 8.68, 3.32, 3.35, 0.76, {
      fontSize: 17, color: p.orangeDark, bold: true, align: "center",
    });

    addText(slide, "你说中文", 8.68, 4.45, 1.95, 0.35, {
      fontSize: 13.5, color: p.ink, bold: true,
    });
    addLine(slide, 10.3, 4.92, 0, 0.34, p.orange, 1.8, "solid", "triangle");
    addRect(slide, 8.68, 5.34, 3.35, 0.9, p.charcoal, 0.14, null, null);
    addText(slide, "英文文字 + 可选英文语音", 8.68, 5.34, 3.35, 0.9, {
      fontSize: 15.5, color: p.white, bold: true, align: "center",
    });
    addPill(slide, "PowerPoint 放映时可显示置顶双语字幕", 8.35, 6.5, 4.05, 0.38, p.light, p.charcoal, 9.5);
    addFooter(slide, p, "TEAMS INTERPRETER / MEETING JOURNEY", 3, false);
    slide.addNotes("会中核心体验是两个方向同时工作：英文来声变中文字幕，中文发言变英文文字，并可经虚拟声卡送出英文语音。");
  }

  // Slide 4
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.charcoal };
    addJourneyHeader(slide, 4, p, true);
    addJourneySlideTitle(slide, "会中增强：听见内容，也理解上下文", "会议助手把字幕、时间和共享页面组合成可检索证据。", p, true);
    addRect(slide, 0.72, 1.7, 4.0, 4.75, p.charcoal2, 0.18, { color: "5A6068", width: 0.8 }, null);
    addText(slide, "共享画面", 1.02, 1.98, 1.2, 0.34, {
      fontSize: 15, color: p.white, bold: true,
    });
    addRect(slide, 1.02, 2.48, 3.4, 1.92, "F3F4F6", 0.1, null, null);
    addRect(slide, 1.24, 2.72, 1.42, 0.96, p.orangeSoft, 0.08, null, null);
    addText(slide, "Q3\n42%", 1.24, 2.72, 1.42, 0.96, {
      fontSize: 20, color: p.orangeDark, bold: true, align: "center", fontFace: FONT_LATIN,
    });
    addRect(slide, 2.88, 2.78, 1.2, 0.18, p.charcoal2, 0.04, null, null);
    addRect(slide, 2.88, 3.14, 0.92, 0.15, p.mid, 0.04, null, null);
    addRect(slide, 2.88, 3.48, 1.05, 0.15, p.mid, 0.04, null, null);
    addText(slide, "页面稳定约 2 秒后自动分析", 1.02, 4.62, 3.4, 0.32, {
      fontSize: 10.2, color: p.mid,
    });
    addPill(slide, "中文速读", 1.02, 5.18, 0.95, 0.34, p.orange, p.white, 8.8);
    addPill(slide, "关键数字", 2.07, 5.18, 0.95, 0.34, "444A52", p.white, 8.8);
    addPill(slide, "术语 / 风险", 3.12, 5.18, 1.02, 0.34, "444A52", p.white, 8.8);
    addText(slide, "仅主动框选区域；未选择时不截图、不调用视觉模型。", 1.02, 5.77, 3.4, 0.48, {
      fontSize: 9.2, color: p.gray, valign: "top",
    });

    addRect(slide, 5.05, 1.7, 7.55, 4.75, "F7F8FA", 0.18, null, shadow("000000", 0.2, 1.4, 45, 0.9));
    addText(slide, "会议助手", 5.42, 2.02, 1.65, 0.36, {
      fontSize: 16, color: p.ink, bold: true,
    });
    ["完整时间线", "会中问答", "实时重点"].forEach((t, i) => {
      addPill(slide, t, 5.42 + i * 1.48, 2.55, 1.28, 0.34, i === 1 ? p.orange : "E4E6E9", i === 1 ? p.white : p.gray, 8.5);
    });
    addRect(slide, 5.42, 3.14, 6.82, 1.2, p.paper, 0.12, { color: p.line, width: 0.8 }, null);
    addText(slide, "问：客户确认的交付节点是什么？", 5.68, 3.36, 5.4, 0.3, {
      fontSize: 11.2, color: p.ink, bold: true,
    });
    addText(slide, "答：会议记录显示计划在本周五完成确认。", 5.68, 3.82, 5.6, 0.28, {
      fontSize: 10.5, color: p.gray,
    });
    addPill(slide, "[10:24 对方]", 10.62, 3.76, 1.25, 0.3, p.orangeSoft, p.orangeDark, 8.3);
    const insights = [
      ["当前议题", "交付计划与范围确认"],
      ["行动项", "周五前确认最终版本"],
      ["风险", "接口权限仍待开通"],
    ];
    insights.forEach((it, i) => {
      const x = 5.42 + i * 2.3;
      addRect(slide, x, 4.72, 2.05, 1.08, i === 1 ? p.orangeSoft : "ECEEF0", 0.12, null, null);
      addText(slide, it[0], x + 0.2, 4.91, 1.6, 0.24, {
        fontSize: 9.2, color: i === 1 ? p.orangeDark : p.gray, bold: true,
      });
      addText(slide, it[1], x + 0.2, 5.27, 1.65, 0.33, {
        fontSize: 9.5, color: p.ink, bold: true,
      });
    });
    addFooter(slide, p, "TEAMS INTERPRETER / MEETING JOURNEY", 4, true);
    slide.addNotes("会议助手提供时间线、证据问答和实时重点。共享画面只在用户主动框选后采样，并在稳定换页后分析。");
  }

  // Slide 5
  {
    const slide = pptx.addSlide();
    slide.background = { color: p.light };
    addJourneyHeader(slide, 5, p, false);
    addJourneySlideTitle(slide, "会后 1 次点击：把会议变成可执行结果", "停止同传后生成结构化纪要，并按需导出关键截图。", p, false);
    const flow = [
      ["停止同传", "等待最后一段翻译完成"],
      ["生成纪要", "短会议一次生成，长会议分段合并"],
      ["核对结果", "摘要 / 结论 / 行动项 / 风险"],
      ["保存交付", "Markdown + 被纪要引用的关键截图"],
    ];
    flow.forEach((f, i) => {
      const x = 0.72 + i * 3.05;
      addBadge(slide, String(i + 1), x, 1.82, 0.58, p, i === 1 ? "solid" : "soft");
      addText(slide, f[0], x, 2.58, 2.55, 0.38, {
        fontSize: 15, color: p.ink, bold: true,
      });
      addText(slide, f[1], x, 3.04, 2.55, 0.64, {
        fontSize: 10.2, color: p.gray, valign: "top",
      });
      if (i < flow.length - 1) addLine(slide, x + 2.5, 2.1, 0.42, 0, p.orange, 1.7, "solid", "triangle");
    });
    addRect(slide, 0.72, 4.15, 7.5, 2.18, p.paper, 0.18, { color: p.line, width: 0.8 }, shadow("000000", 0.08, 1.2, 45, 0.8));
    addText(slide, "AI 会议纪要", 1.02, 4.48, 2.15, 0.38, {
      fontSize: 17, color: p.ink, bold: true,
    });
    const sections = ["会议概览", "核心摘要", "决策与结论", "行动项", "风险与未决问题", "共享画面要点"];
    sections.forEach((t, i) => {
      addPill(slide, t, 1.02 + (i % 3) * 2.15, 5.08 + Math.floor(i / 3) * 0.55, 1.85, 0.36, i === 3 ? p.orange : p.light, i === 3 ? p.white : p.charcoal, 8.8);
    });
    addRect(slide, 8.62, 4.15, 3.98, 2.18, p.charcoal, 0.18, null, null);
    addText(slide, "隐私与边界", 8.98, 4.48, 2.2, 0.35, {
      fontSize: 16, color: p.white, bold: true,
    });
    const boundaries = [
      "默认不录制音频、不落盘",
      "截图仅在主动导出后保存",
      "使用前需取得参会者授权",
    ];
    boundaries.forEach((t, i) => {
      slide.addShape("ellipse", {
        x: 9.0, y: 5.12 + i * 0.4, w: 0.1, h: 0.1,
        fill: { color: p.orange }, line: noLine(),
      });
      addText(slide, t, 9.25, 5.0 + i * 0.4, 2.95, 0.3, {
        fontSize: 9.5, color: p.mid,
      });
    });
    addFooter(slide, p, "TEAMS INTERPRETER / MEETING JOURNEY", 5, false);
    slide.addNotes("会后点击生成纪要，输出结构化 Markdown；只有被纪要引用的页面截图会被导出，避免无关图片落盘。");
  }

  return { pptx, file: path.join(OUT_DIR, "版本C_会议旅程型_项目介绍.pptx") };
}

async function main() {
  const decks = [buildValueDeck(), buildTechDeck(), buildJourneyDeck()];
  for (const deck of decks) {
    await deck.pptx.writeFile({ fileName: deck.file });
    console.log(`Created ${deck.file}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
