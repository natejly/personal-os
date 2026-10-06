const esc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** printToPDF footer: a small grey "title · page N", with the title escaped and cut to fit one line. */
export function footerTemplate(title: string): string {
  const t = title.length > 70 ? `${title.slice(0, 69)}…` : title
  return `<div style="width:100%;font-size:8px;color:#777;text-align:center;font-family:-apple-system,Helvetica,Arial,sans-serif"><span>${esc(t)}</span> · <span class="pageNumber"></span></div>`
}
