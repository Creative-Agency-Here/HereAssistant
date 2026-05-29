// Рендер markdown → HTML для просмотра диалогов (таблицы, код, списки, ссылки).
import MarkdownIt from 'markdown-it'

let _md: MarkdownIt | null = null

function getMd(): MarkdownIt {
  if (!_md) {
    _md = new MarkdownIt({
      html: false,    // сырой HTML из контента не рендерим (безопасность)
      linkify: true,  // голые ссылки → кликабельные
      breaks: true,   // перенос строки = <br>, как в чате
    })
  }
  return _md
}

export function renderMarkdown(text: string): string {
  return getMd().render(text || '')
}
