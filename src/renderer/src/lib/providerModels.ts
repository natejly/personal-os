/** Which models a model setting may choose from. */
export type PickKind = 'chat' | 'embedding' | 'rerank' | 'image'

/** The model fields of Settings, in the order the Model tab shows them. `blank` is the empty option's label. */
export const MODEL_FIELDS = [
  { key: 'defaultModel', label: 'Chat model', kind: 'chat', blank: '', help: '' },
  { key: 'modelHigh', label: 'High tier', kind: 'chat', blank: 'Provider default', help: 'Drafting skills and agents.', tier: 'high' },
  { key: 'modelMedium', label: 'Medium tier', kind: 'chat', blank: 'Provider default', help: 'Auto-review of tool calls, summaries and recaps, memory tidy-ups, and short messages under Auto.', tier: 'medium' },
  { key: 'modelLow', label: 'Low tier', kind: 'chat', blank: 'Provider default', help: 'Titles, memories, follow-ups and other background learning.', tier: 'low' },
  { key: 'workerModel', label: 'Default worker model', kind: 'chat', blank: 'Same as chat', help: "Model for workers the chat starts. An agent's own model still wins." },
  { key: 'embeddingModel', label: 'Search model', kind: 'embedding', blank: 'None: keyword search', help: 'Chosen for you from your providers. Once the search index is built it is fixed; before that you can change it.' },
  { key: 'retrievalRerankModel', label: 'Rerank model', kind: 'rerank', blank: 'Provider default', help: 'Reorders memory and document search results by relevance.' },
  { key: 'visionModel', label: 'Vision model', kind: 'chat', blank: 'Same as chat', help: 'Reads pictures. Empty uses the chat model when it can read images; otherwise pictures are read with OCR only.' },
  { key: 'imageModel', label: 'Image model', kind: 'image', blank: 'Not set', help: 'Used when the assistant makes an image. The provider must offer an OpenAI-compatible images endpoint.' }
] as const

export type ModelKey = (typeof MODEL_FIELDS)[number]['key']
