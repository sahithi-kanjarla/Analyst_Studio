import { defaultSchema, type Options } from "rehype-sanitize";

/** What raw HTML inside the markdown may keep. Values from an uploaded file (a product name, a category) can end up in the report text,
 *  so anything that can run code (script, event handlers such as onerror, javascript: links, iframes) is removed.
 *  The report's own <details> and <summary> stay, and <details> keeps its class so the card styling still applies. */
export const mdSchema: Options = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "details", "summary"],
  attributes: {
    ...defaultSchema.attributes,
    details: ["open", "className"],
    summary: ["className"],
  },
};
