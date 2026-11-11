"""Small helpers shared by the browser test modules."""

VIEWPORT = {"width": 1280, "height": 800}


def next_frames(page, count=2):
    page.evaluate(
        """(count) => new Promise(resolve => {
          const step = left => left ? requestAnimationFrame(() => step(left - 1)) : resolve();
          step(count);
        })""",
        count,
    )


def set_range(page, selector, value):
    page.eval_on_selector(
        selector,
        "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); }",
        str(value),
    )
