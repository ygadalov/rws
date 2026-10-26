/* ──────────────────────────────────────────────────────────────
   info-tooltip — подсказки по кнопке (i)

   Зачем слой, а не пузырь на месте: пузырь внутри кнопки обрезается
   любым предком с overflow (панель, прокручиваемый список, таблица),
   а на краю экрана уезжает за границу. Здесь один общий
   position:fixed узел в <body>, который зажимается по горизонтали
   и переворачивается вверх, если снизу не помещается.

   Текст берётся либо из вложенного .info-bubble (разметка внутри
   сохраняется), либо из атрибута data-tip.

   ВНИМАНИЕ: по умолчанию текст вставляется как HTML — чтобы внутри
   подсказок работали <b> и <span class="em">. Это безопасно, пока
   строки свои, авторские. Если подсказка может прийти из
   пользовательского ввода или из внешних данных — включите
   { html: false }, иначе получите XSS.

   Использование:
       <button class="info-tip" type="button" aria-label="Что это">
         <svg …>…</svg>
         <span class="info-bubble" role="tooltip">
           Текст с <b>выделением</b>.
         </span>
       </button>

       initInfoTooltips();                       // с умолчаниями
       initInfoTooltips({ trigger: '.my-hint' }); // свой селектор

   Вернёт { destroy() } — снимает обработчики и убирает слой.
   ────────────────────────────────────────────────────────────── */

function initInfoTooltips(options) {
    var o = options || {};
    var TRIGGER   = o.trigger   || '.info-tip';
    // Источники текста внутри триггера, в порядке приоритета.
    var SOURCE    = o.source    || '.info-bubble';
    // Чем ограничивать подсказку по горизонтали, кроме окна: полезно,
    // когда контент уже окна (есть сайдбар) — иначе подсказка заезжает на него.
    var HOST      = o.host      || null;
    var AS_HTML   = o.html !== false;
    // Элементы, для которых клик не должен открывать подсказку: обычно это
    // триггеры, у которых есть собственное действие по клику.
    var NO_CLICK  = o.noClick   || null;
    var MARGIN    = o.margin    != null ? o.margin : 8;   // отступ от краёв окна
    var GAP       = o.gap       != null ? o.gap    : 9;   // зазор до значка
    var doc       = o.document  || document;

    var layer = doc.createElement('div');
    layer.className = 'tt';
    layer.innerHTML = '<div class="tt-body"></div><div class="tt-arrow"></div>';
    doc.body.appendChild(layer);
    var body  = layer.querySelector('.tt-body');
    var arrow = layer.querySelector('.tt-arrow');

    var current = null;
    // Подсказку открыл focusin, и клик её ещё не «съел». Нужно потому, что
    // касание приходит как focusin, а следом click по тому же значку: без флага
    // первый тап мигнул бы и закрылся. Он же разрешает прокрутке переставлять
    // подсказку вместо закрытия, пока значок в фокусе, — иначе Tab к значку вне
    // видимой области не показал бы ничего: браузер подкручивает элемент к себе,
    // прокрутка срабатывает и гасит подсказку в тот же момент.
    var focusOpened = false;
    // Тип указателя последнего нажатия. focusin приходит и при клике мышью, так что
    // отличить «клик мышью по уже открытой» от «первого тапа» можно только по нему.
    var lastPointer = 'mouse';

    function textOf(trigger) {
        var src = trigger.querySelector(SOURCE);
        if (src) return AS_HTML ? src.innerHTML : src.textContent;
        return trigger.getAttribute('data-tip') || '';
    }

    function show(trigger) {
        var content = textOf(trigger);
        if (!content) return;
        current = trigger;
        if (AS_HTML) body.innerHTML = content; else body.textContent = content;

        // Показываем до замеров: у скрытого узла нулевая ширина.
        layer.classList.add('show');

        var t = trigger.getBoundingClientRect();
        var b = layer.getBoundingClientRect();

        // Горизонталь: по центру значка, но не вылезая ни за окно, ни за host.
        var loBound = MARGIN, hiBound = window.innerWidth - b.width - MARGIN;
        if (HOST) {
            var host = trigger.closest(HOST);
            if (host) {
                var hr = host.getBoundingClientRect();
                loBound = Math.max(loBound, hr.left + 4);
                hiBound = Math.min(hiBound, hr.right - b.width - 4);
            }
        }
        var centered = t.left + t.width / 2 - b.width / 2;
        var left = Math.max(loBound, Math.min(centered, hiBound));
        // Если host уже подсказки, hiBound уходит ниже loBound — не даём отрицательной ширине
        // выбросить подсказку влево за экран.
        if (hiBound < loBound) left = loBound;

        // Вертикаль: под значком, а если снизу не влезает и сверху влезает — над ним.
        var below = t.bottom + GAP;
        var above = t.top - GAP - b.height;
        var flip = (below + b.height > window.innerHeight - MARGIN) && above >= MARGIN;
        layer.classList.toggle('flip', flip);

        layer.style.left = Math.round(left) + 'px';
        layer.style.top  = Math.round(flip ? above : below) + 'px';

        // Стрелка остаётся под значком, даже когда подсказку сдвинул клэмп.
        var ax = Math.max(12, Math.min(t.left + t.width / 2 - left, b.width - 12));
        arrow.style.left = (ax - 6) + 'px';
    }

    function hide() {
        layer.classList.remove('show');
        current = null;
        focusOpened = false;
    }

    function onScroll() {
        if (!current) return;
        // Значок в фокусе — он едет вместе с содержимым, подсказку переставляем.
        if (focusOpened && doc.activeElement === current) show(current);
        else hide();
    }

    function closest(e) {
        return (e.target instanceof Element) ? e.target.closest(TRIGGER) : null;
    }

    var on = {
        // Только мышь. Касание тоже присылает pointerover/pointerout, и если
        // реагировать на них, подсказка открывается по наведению и гаснет
        // следующим за тем же тапом кликом — первое касание не даёт ничего.
        // Для касания работает только ветка click ниже.
        pointerdown: function (e) { lastPointer = e.pointerType || 'mouse'; },
        // Наведение — только мышь. Касание тоже присылает pointerover/pointerout,
        // и реакция на них спорила бы с веткой click.
        pointerover: function (e) {
            if (e.pointerType && e.pointerType !== 'mouse') return;
            var it = closest(e);
            if (it && it !== current) { show(it); focusOpened = false; }
        },
        pointerout: function (e) {
            if (e.pointerType && e.pointerType !== 'mouse') return;
            var it = closest(e);
            // Переход на собственного потомка триггера — не уход.
            if (it && it === current && !it.contains(e.relatedTarget)) hide();
        },
        focusin:  function (e) { var it = closest(e); if (it) { show(it); focusOpened = true; } },
        focusout: function (e) { var it = closest(e); if (it && it === current) hide(); },
        click: function (e) {
            var it = closest(e);
            if (it && !(NO_CLICK && it.matches(NO_CLICK))) {
                e.preventDefault();
                if (current !== it) {
                    show(it);
                } else if (lastPointer !== 'mouse' && focusOpened) {
                    // Первый тап: focusin уже открыл подсказку. Переключив здесь,
                    // мы бы мигнули и закрыли. Оставляем, но снимаем признак —
                    // следующий тап по тому же значку закроет.
                    focusOpened = false;
                } else {
                    // Мышь: подсказка и так видна по наведению, клик её убирает.
                    hide();
                }
            } else if (!layer.contains(e.target)) {
                hide();
            }
        },
        keydown: function (e) { if (e.key === 'Escape') hide(); }
    };

    Object.keys(on).forEach(function (type) { doc.addEventListener(type, on[type]); });
    // true — ловим прокрутку любого вложенного контейнера, не только окна:
    // подсказка висит в фиксированных координатах и за содержимым не едет.
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', hide);

    return {
        show: show,
        hide: hide,
        destroy: function () {
            Object.keys(on).forEach(function (type) { doc.removeEventListener(type, on[type]); });
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', hide);
            layer.remove();
            current = null;
        }
    };
}

/* Значок (i) — тот же контур, что в проекте. Вставлять внутрь .info-tip. */
var INFO_TIP_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/>' +
    '<line x1="12" y1="8" x2="12" y2="8.01"/></svg>';

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { initInfoTooltips: initInfoTooltips, INFO_TIP_SVG: INFO_TIP_SVG };
}
