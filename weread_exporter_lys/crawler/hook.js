(() => {
  if (window.wrpaHandler) {
    return;
  }

  const lines = [];
  const lineRecords = [];
  let lineBuffer = [];
  let currentY = null;

  function parseFontSize(font) {
    const match = String(font || '').match(/(\d+(?:\.\d+)?)px/);
    return match ? Number(match[1]) : 16;
  }

  function isFontProbeLine(text) {
    return /abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ/.test(text);
  }

  function flushLine() {
    if (!lineBuffer.length) {
      return;
    }

    lineBuffer.sort((left, right) => left.x - right.x);
    const text = lineBuffer.map((item) => item.text).join('').trim();
    if (text && !isFontProbeLine(text)) {
      const maxFontSize = Math.max(...lineBuffer.map((item) => item.fontSize));
      const minFontSize = Math.min(...lineBuffer.map((item) => item.fontSize));
      // Representative y for the line: the min y of its fragments (top-most),
      // matching how canvas rows align with the rare-char <img> translate y.
      const minY = lineBuffer.reduce((acc, item) => Math.min(acc, item.y), Infinity);
      let prefix = '';
      if (maxFontSize >= 27) {
        prefix = '## ';
      } else if (maxFontSize >= 23) {
        prefix = '### ';
      }
      lines.push(`${prefix}${text}`);
      lineRecords.push({
        text,
        y: minY,
        fontSize: maxFontSize,
        minFontSize,
        prefix,
      });
    }

    lineBuffer = [];
  }

  function installCanvasHook(context) {
    if (!context || context.__wrpaHookInstalled) {
      return context;
    }

    const originalFillText = context.fillText;
    context.fillText = function patchedFillText(text, x, y, ...rest) {
      const normalizedY = Math.round(Number(y) || 0);
      if (currentY !== null && Math.abs(normalizedY - currentY) > 4) {
        flushLine();
      }
      currentY = normalizedY;
      lineBuffer.push({
        text: String(text || ''),
        x: Number(x) || 0,
        y: normalizedY,
        fontSize: parseFontSize(this.font),
      });
      _onRenderActivity();
      return originalFillText.call(this, text, x, y, ...rest);
    };

    const originalRestore = context.restore;
    context.restore = function patchedRestore(...args) {
      flushLine();
      _onRenderActivity();
      return originalRestore.call(this, ...args);
    };

    context.__wrpaHookInstalled = true;
    return context;
  }

  const originalGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function patchedGetContext(type, ...args) {
    const context = originalGetContext.call(this, type, ...args);
    if (type === '2d') {
      return installCanvasHook(context);
    }
    return context;
  };

  let _renderTimer = null;
  function _onRenderActivity() {
    window.__wrpaRenderStable = false;
    if (_renderTimer) clearTimeout(_renderTimer);
    _renderTimer = setTimeout(() => {
      window.__wrpaRenderStable = true;
    }, 300);
  }

  window.wrpaHandler = {
    detect() {
      return !!window.__WRPA__;
    },
    getVersion() {
      return window.__WRPA__ ? window.__WRPA__.version : null;
    },
    getMarkdown() {
      flushLine();
      return Array.from(new Set(lines)).join('\n\n');
    },
    getLinesWithCoords() {
      flushLine();
      // Return a deep-ish copy so callers can't mutate internal state.
      return lineRecords.map((record) => ({ ...record }));
    },
    clearMarkdown() {
      lines.length = 0;
      lineRecords.length = 0;
      lineBuffer.length = 0;
      currentY = null;
      window.__wrpaRenderStable = false;
    },
    getAntiCrawlStatus() {
      flushLine();
      const canvases = Array.from(document.querySelectorAll('canvas'));
      return {
        hasWRPA: !!window.__WRPA__,
        wrpaVersion: window.__WRPA__ ? window.__WRPA__.version : null,
        hasPaywall: !!document.body.innerText.match(/付费|购买|会员|登录/),
        hasCanvasContent: canvases.length > 0,
        hasCanvasHandler: true,
        hasCanvasMarkdown: lines.length > 0,
      };
    },
  };
})();
