/** The characters a `#tag` may hold. The editor highlights with it; `extract_tags` in backend/personal_os/docs.py indexes with the same shape, so keep the two in step. */

export const TAG_BODY = '[\\p{L}][\\p{L}\\p{N}_/-]*'
