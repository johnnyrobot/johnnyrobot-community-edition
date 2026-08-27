/**
 * The GraphBuildManifest collection (the graph-build determinism contract).
 *
 * Content-free by construction: identities, counts, digests, and warnings, and
 * never a heading, title, or excerpt. A manifest that carried content would be
 * a second place a Course Material could leak from, and it is written on every
 * build of every material.
 *
 * Superuser-only like every other business collection -- FastAPI is the sole
 * front door (the private persistence boundary).
 */
migrate((app) => {
  const students = app.findCollectionByNameOrId("users");

  const manifests = new Collection({ name: "graph_build_manifests", type: "base" });
  manifests.listRule = null;
  manifests.viewRule = null;
  manifests.createRule = null;
  manifests.updateRule = null;
  manifests.deleteRule = null;

  manifests.fields.add(new RelationField({
    name: "student", required: true, maxSelect: 1,
    collectionId: students.id, cascadeDelete: true,
  }));
  manifests.fields.add(new TextField({ name: "material", required: true, max: 32 }));
  manifests.fields.add(new NumberField({ name: "generation" }));
  manifests.fields.add(new TextField({ name: "policy_id", max: 64 }));
  manifests.fields.add(new TextField({ name: "parser_version", max: 64 }));
  manifests.fields.add(new TextField({ name: "extraction_policy_id", max: 64 }));
  manifests.fields.add(new TextField({ name: "extraction_model", max: 128 }));
  manifests.fields.add(new TextField({ name: "source_digest", max: 128 }));
  manifests.fields.add(new TextField({ name: "sections_digest", max: 128 }));
  manifests.fields.add(new NumberField({ name: "section_count" }));
  manifests.fields.add(new NumberField({ name: "concepts_accepted" }));
  manifests.fields.add(new NumberField({ name: "edges_accepted" }));
  manifests.fields.add(new NumberField({ name: "candidates_rejected" }));
  manifests.fields.add(new SelectField({
    name: "outcome", maxSelect: 1,
    values: ["built", "failed", "skipped_unsupported_format", "skipped_no_graph"],
  }));
  // Warning strings only -- reasons and counts, never rejected excerpt text.
  manifests.fields.add(new JSONField({ name: "warnings", maxSize: 20000 }));
  manifests.fields.add(new AutodateField({ name: "created", onCreate: true }));
  manifests.fields.add(new AutodateField({ name: "updated", onCreate: true, onUpdate: true }));
  app.save(manifests);
}, (app) => {
  app.delete(app.findCollectionByNameOrId("graph_build_manifests"));
});
