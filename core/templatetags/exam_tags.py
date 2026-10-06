import html
import re
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

FENCED_CODE_PATTERN = re.compile(r'```([a-zA-Z0-9_+-]*)\n?(.*?)```', re.DOTALL)


def _build_code_box(code_content: str, lang: str) -> str:
    escaped_code = html.escape(code_content)
    lang_label = html.escape(lang.upper()) if lang else "CODE"
    return (
        f'<div class="exam-code-box my-4 rounded-2xl border border-white/15 bg-black/60 shadow-inner overflow-hidden font-mono">'
        f'<div class="exam-code-header flex items-center justify-between px-4 py-2 border-b border-white/10 bg-white/[0.04]">'
        f'<div class="flex items-center gap-1.5">'
        f'<span class="w-2.5 h-2.5 rounded-full bg-red-400/80 inline-block"></span>'
        f'<span class="w-2.5 h-2.5 rounded-full bg-amber-400/80 inline-block"></span>'
        f'<span class="w-2.5 h-2.5 rounded-full bg-emerald-400/80 inline-block"></span>'
        f'</div>'
        f'<span class="text-[11px] font-mono tracking-wider uppercase text-examaccent/90 font-semibold">{lang_label}</span>'
        f'</div>'
        f'<pre class="exam-code-content p-4 text-[#a7f3d0] overflow-x-auto text-xs sm:text-sm leading-relaxed whitespace-pre font-mono select-none"><code>{escaped_code}</code></pre>'
        f'</div>'
    )


@register.filter(name="render_question_text")
def render_question_text(value: str) -> str:
    if not value:
        return ""

    # 1. Fenced markdown code blocks
    if "```" in value:
        parts = []
        last_idx = 0
        for match in FENCED_CODE_PATTERN.finditer(value):
            prefix = value[last_idx:match.start()].strip()
            if prefix:
                escaped_prefix = html.escape(prefix).replace("\n", "<br>")
                parts.append(f'<p class="exam-question text-lg text-white leading-relaxed mb-3">{escaped_prefix}</p>')

            lang = match.group(1).strip() or "code"
            code_content = match.group(2).strip()
            parts.append(_build_code_box(code_content, lang))
            last_idx = match.end()

        suffix = value[last_idx:].strip()
        if suffix:
            escaped_suffix = html.escape(suffix).replace("\n", "<br>")
            parts.append(f'<p class="exam-question-suffix text-base text-white/90 leading-relaxed mt-3 mb-6">{escaped_suffix}</p>')

        return mark_safe("".join(parts))

    # 2. Implicit code block: prompt followed by \n\n and code
    if "\n\n" in value:
        chunks = value.split("\n\n", 1)
        prompt_part = chunks[0].strip()
        code_part = chunks[1].strip()
        code_indicators = (
            "function", "console.", "def ", "print(", "return ", "let ",
            "const ", "var ", "=>", "class ", "import ", "<?php",
            "public static void", "System.out",
        )
        if any(ind in code_part for ind in code_indicators):
            lead_html = f'<p class="exam-question text-lg text-white leading-relaxed mb-3">{html.escape(prompt_part).replace(chr(10), "<br>")}</p>'
            detected_lang = "python" if ("def " in code_part or "print(" in code_part) else "javascript"
            return mark_safe(lead_html + _build_code_box(code_part, detected_lang))

    # 3. Standard text question
    escaped = html.escape(value).replace("\n", "<br>")
    return mark_safe(f'<p class="exam-question text-lg text-white leading-relaxed mb-6">{escaped}</p>')
