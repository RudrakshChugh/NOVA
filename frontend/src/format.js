// Chunk page refs are 'p3', 'p3-p5' (PDF pages) or 'para7', 'para7-para9' (Word paragraphs).
export const pageLabel = (ref) =>
  (ref.startsWith('para') ? `paragraph ${ref.replace(/para/g, '')}` : `page ${ref.replace(/p/g, '')}`).replace('-', '–')

export const pct = (x) => `${Math.round(x * 100)}%`

export const FIELD_LABEL = {
  bloom_level: 'Bloom level', question_type: 'Question type', marks: 'Marks weighting',
  concept: 'Concept', course_outcome: 'Course outcome', grounding: 'Grounded in material',
}
