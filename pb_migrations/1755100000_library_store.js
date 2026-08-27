/**
 * Each Student Library records the File Search store that holds it (per-Library store isolation).
 *
 * The per-Library search boundary prohibits "a shared global index protected only by caller-supplied
 * metadata filters" and the per-Library provider-store boundary prohibits "shared cross-Library stores"; until
 * this field existed the implementation had exactly the shape both forbid. The
 * store cannot be derived from the Student identity — Gemini appends an opaque
 * suffix to every store it creates — and it must not be discovered by display
 * name, which the per-Library provider-store boundary also prohibits. So it is recorded here, and this record
 * is the only authority on which store belongs to which Library.
 *
 * Empty means the Student has never uploaded. The search path reads that as an
 * empty Library and offers no search tool at all; only an upload provisions a
 * store. See api/services/gemini_service.py.
 *
 * A separate migration rather than an edit to 1755000000_collections.js:
 * PocketBase records which migrations it has applied, so amending an applied
 * one would leave every existing deployment without the field.
 */
migrate((app) => {
  const students = app.findCollectionByNameOrId("users");
  students.fields.add(new TextField({ name: "library_store_name", max: 200 }));
  app.save(students);

  // The Document an import created, which is what search actually reads.
  //
  // It outlives the file it was imported from: measured against live Gemini,
  // a query still returned a material's content after `files.delete` and
  // stopped only once the Document was deleted. Removal therefore needs this
  // name, and the import operation is the only place the provider reports it.
  const materials = app.findCollectionByNameOrId("course_materials");
  materials.fields.add(new TextField({ name: "provider_document_name", max: 300 }));
  app.save(materials);
}, (app) => {
  const students = app.findCollectionByNameOrId("users");
  students.fields.removeByName("library_store_name");
  app.save(students);

  const materials = app.findCollectionByNameOrId("course_materials");
  materials.fields.removeByName("provider_document_name");
  app.save(materials);
});
