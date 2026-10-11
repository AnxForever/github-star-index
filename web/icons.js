/*
 * Small, dependency-free SVG helper using Lucide icon geometry.
 * Icon path data is derived from lucide-react 0.468.0; see THIRD_PARTY_NOTICES.txt.
 */
(function (global) {
  'use strict';

  const ns = 'http://www.w3.org/2000/svg';
  const icons = {
    'search': [['circle', { cx: 11, cy: 11, r: 8 }], ['path', { d: 'm21 21-4.3-4.3' }]],
    'arrow-up-right': [['path', { d: 'M7 7h10v10' }], ['path', { d: 'M7 17 17 7' }]],
    'arrow-right': [['path', { d: 'M5 12h14' }], ['path', { d: 'm12 5 7 7-7 7' }]],
    'star': [['path', { d: 'M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z' }]],
    'bookmark': [['path', { d: 'm19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z' }]],
    'library': [['path', { d: 'm16 6 4 14' }], ['path', { d: 'M12 6v14' }], ['path', { d: 'M8 8v12' }], ['path', { d: 'M4 4v16' }]],
    'code-2': [['path', { d: 'm18 16 4-4-4-4' }], ['path', { d: 'm6 8-4 4 4 4' }], ['path', { d: 'm14.5 4-5 16' }]],
    'terminal': [['polyline', { points: '4 17 10 11 4 5' }], ['line', { x1: 12, x2: 20, y1: 19, y2: 19 }]],
    'boxes': [
      ['path', { d: 'M2.97 12.92A2 2 0 0 0 2 14.63v3.24a2 2 0 0 0 .97 1.71l3 1.8a2 2 0 0 0 2.06 0L12 19v-5.5l-5-3-4.03 2.42Z' }],
      ['path', { d: 'm7 16.5-4.74-2.85' }], ['path', { d: 'm7 16.5 5-3' }], ['path', { d: 'M7 16.5v5.17' }],
      ['path', { d: 'M12 13.5V19l3.97 2.38a2 2 0 0 0 2.06 0l3-1.8a2 2 0 0 0 .97-1.71v-3.24a2 2 0 0 0-.97-1.71L17 10.5l-5 3Z' }],
      ['path', { d: 'm17 16.5-5-3' }], ['path', { d: 'm17 16.5 4.74-2.85' }], ['path', { d: 'M17 16.5v5.17' }],
      ['path', { d: 'M7.97 4.42A2 2 0 0 0 7 6.13v4.37l5 3 5-3V6.13a2 2 0 0 0-.97-1.71l-3-1.8a2 2 0 0 0-2.06 0l-3 1.8Z' }],
      ['path', { d: 'M12 8 7.26 5.15' }], ['path', { d: 'm12 8 4.74-2.85' }], ['path', { d: 'M12 13.5V8' }]
    ],
    'book-open': [['path', { d: 'M12 7v14' }], ['path', { d: 'M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z' }]],
    'sliders-horizontal': [
      ['line', { x1: 21, x2: 14, y1: 4, y2: 4 }], ['line', { x1: 10, x2: 3, y1: 4, y2: 4 }],
      ['line', { x1: 21, x2: 12, y1: 12, y2: 12 }], ['line', { x1: 8, x2: 3, y1: 12, y2: 12 }],
      ['line', { x1: 21, x2: 16, y1: 20, y2: 20 }], ['line', { x1: 12, x2: 3, y1: 20, y2: 20 }],
      ['line', { x1: 14, x2: 14, y1: 2, y2: 6 }], ['line', { x1: 8, x2: 8, y1: 10, y2: 14 }], ['line', { x1: 16, x2: 16, y1: 18, y2: 22 }]
    ],
    'moon': [['path', { d: 'M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z' }]],
    'sun': [['circle', { cx: 12, cy: 12, r: 4 }], ['path', { d: 'M12 2v2' }], ['path', { d: 'M12 20v2' }], ['path', { d: 'm4.93 4.93 1.41 1.41' }], ['path', { d: 'm17.66 17.66 1.41 1.41' }], ['path', { d: 'M2 12h2' }], ['path', { d: 'M20 12h2' }], ['path', { d: 'm6.34 17.66-1.41 1.41' }], ['path', { d: 'm19.07 4.93-1.41 1.41' }]],
    'x': [['path', { d: 'M18 6 6 18' }], ['path', { d: 'm6 6 12 12' }]],
    'check': [['path', { d: 'M20 6 9 17l-5-5' }]],
    'plus': [['path', { d: 'M5 12h14' }], ['path', { d: 'M12 5v14' }]],
    'chevron-down': [['path', { d: 'm6 9 6 6 6-6' }]],
    'github': [['path', { d: 'M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4' }], ['path', { d: 'M9 18c-4.51 2-5-2-7-2' }]],
    'git-compare-arrows': [['circle', { cx: 5, cy: 6, r: 3 }], ['path', { d: 'M12 6h5a2 2 0 0 1 2 2v7' }], ['path', { d: 'm15 9-3-3 3-3' }], ['circle', { cx: 19, cy: 18, r: 3 }], ['path', { d: 'M12 18H7a2 2 0 0 1-2-2V9' }], ['path', { d: 'm9 15 3 3-3 3' }]],
    'command': [['path', { d: 'M15 6v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3' }]]
  };

  function create(name, { size = 20, className = '' } = {}) {
    const key = Object.prototype.hasOwnProperty.call(icons, name) ? name : 'bookmark';
    const dimension = typeof size === 'number' && Number.isFinite(size) && size > 0
      ? String(size)
      : (typeof size === 'string' && /^(?:\d+(?:\.\d+)?)(?:px|em|rem|%)?$/.test(size) ? size : '20');
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('xmlns', ns);
    svg.setAttribute('width', dimension);
    svg.setAttribute('height', dimension);
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    if (className) svg.setAttribute('class', String(className));

    for (const [tag, attributes] of icons[key]) {
      const shape = document.createElementNS(ns, tag);
      for (const [attribute, value] of Object.entries(attributes)) {
        shape.setAttribute(attribute, String(value));
      }
      svg.appendChild(shape);
    }
    return svg;
  }

  function hydrate(root = document) {
    const placeholders = [];
    if (root && root.nodeType === 1 && root.matches('[data-icon]')) placeholders.push(root);
    if (root && typeof root.querySelectorAll === 'function') {
      placeholders.push(...root.querySelectorAll('[data-icon]'));
    }
    for (const placeholder of placeholders) {
      const size = placeholder.getAttribute('data-size');
      const svg = create(placeholder.getAttribute('data-icon'), {
        size: size || 20,
        className: placeholder.getAttribute('class') || ''
      });
      placeholder.replaceWith(svg);
    }
    return placeholders.length;
  }

  global.StarIcons = Object.freeze({ create, hydrate });
})(window);
