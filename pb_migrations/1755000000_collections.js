/**
 * Collections for the community-single-site@1 binding (the private persistence boundary).
 *
 * Every business collection is superuser-only: FastAPI is the sole front door
 * and the browser never holds a PocketBase client. The users collection keeps
 * its create rule locked because a Deployment Operator provisions every
 * Student and there is no self-registration (the reset-only demo profile).
 */
migrate((app) => {
  const students = app.findCollectionByNameOrId("users");
  students.createRule = null;   // provisioning is an operator action
  students.listRule = null;
  students.viewRule = null;
  students.updateRule = null;
  students.deleteRule = null;
  students.fields.add(new TextField({ name: "preferred_language", max: 16 }));
  students.fields.add(new JSONField({ name: "preferences", maxSize: 20000 }));
  app.save(students);

  const owner = (collection) =>
    new RelationField({
      name: "student",
      required: true,
      maxSelect: 1,
      collectionId: students.id,
      cascadeDelete: true,
    });

  const locked = (collection) => {
    collection.listRule = null;
    collection.viewRule = null;
    collection.createRule = null;
    collection.updateRule = null;
    collection.deleteRule = null;
    return collection;
  };

  const materials = locked(new Collection({ name: "course_materials", type: "base" }));
  materials.fields.add(owner(materials));
  materials.fields.add(new TextField({ name: "title", required: true, max: 500 }));
  materials.fields.add(new TextField({ name: "source_identity", max: 500 }));
  materials.fields.add(new TextField({ name: "material_source", max: 50 }));
  materials.fields.add(new SelectField({
    name: "status", maxSelect: 1, values: ["processing", "ready", "failed"],
  }));
  materials.fields.add(new TextField({ name: "provider_file_name", max: 200 }));
  materials.fields.add(new TextField({ name: "provider_uri", max: 500 }));
  materials.fields.add(new TextField({ name: "provider_store_name", max: 200 }));
  // created/updated are optional autodate fields as of PocketBase 0.23+
  // -- a new base collection does not get them for free, and Repository.
  // list_materials sorts by "created", so leaving them undeclared would sort
  // on a column that does not exist.
  materials.fields.add(new AutodateField({ name: "created", onCreate: true }));
  materials.fields.add(new AutodateField({ name: "updated", onCreate: true, onUpdate: true }));
  // Partial: a direct upload carries no Source Identity, and PocketBase stores
  // an unset text field as '' rather than NULL, so a plain unique index would
  // reject the second direct upload.
  materials.indexes = [
    "CREATE UNIQUE INDEX idx_material_source_identity ON course_materials (student, source_identity) WHERE source_identity != ''",
    "CREATE INDEX idx_material_student ON course_materials (student)",
  ];
  app.save(materials);

  const tokens = locked(new Collection({ name: "canvas_tokens", type: "base" }));
  tokens.fields.add(owner(tokens));
  tokens.fields.add(new TextField({ name: "canvas_url", max: 500 }));
  tokens.fields.add(new TextField({ name: "api_token_ciphertext", max: 2000 }));
  tokens.fields.add(new NumberField({ name: "key_version" }));
  tokens.fields.add(new BoolField({ name: "disconnected" }));
  tokens.fields.add(new TextField({ name: "last_sync", max: 64 }));
  // created/updated are optional autodate fields as of PocketBase 0.23+.
  tokens.fields.add(new AutodateField({ name: "created", onCreate: true }));
  tokens.fields.add(new AutodateField({ name: "updated", onCreate: true, onUpdate: true }));
  tokens.indexes = ["CREATE UNIQUE INDEX idx_canvas_token_student ON canvas_tokens (student)"];
  app.save(tokens);

  const canvasData = locked(new Collection({ name: "canvas_data", type: "base" }));
  canvasData.fields.add(owner(canvasData));
  canvasData.fields.add(new TextField({ name: "data_type", max: 50 }));
  canvasData.fields.add(new TextField({ name: "canvas_id", max: 100 }));
  canvasData.fields.add(new TextField({ name: "course_id", max: 100 }));
  canvasData.fields.add(new TextField({ name: "course_name", max: 500 }));
  canvasData.fields.add(new TextField({ name: "title", max: 500 }));
  canvasData.fields.add(new TextField({ name: "content", max: 200000 }));
  canvasData.fields.add(new TextField({ name: "due_date", max: 64 }));
  canvasData.fields.add(new JSONField({ name: "metadata", maxSize: 200000 }));
  // created/updated are optional autodate fields as of PocketBase 0.23+
  // -- CanvasDataResponse maps them to created_at/updated_at, and
  // Repository.list_canvas_records sorts by "created".
  canvasData.fields.add(new AutodateField({ name: "created", onCreate: true }));
  canvasData.fields.add(new AutodateField({ name: "updated", onCreate: true, onUpdate: true }));
  canvasData.indexes = [
    "CREATE UNIQUE INDEX idx_canvas_data_source ON canvas_data (student, data_type, canvas_id)",
  ];
  app.save(canvasData);

  const sessions = locked(new Collection({ name: "sessions", type: "base" }));
  sessions.fields.add(owner(sessions));
  sessions.fields.add(new TextField({ name: "room_name", required: true, max: 200 }));
  sessions.fields.add(new TextField({ name: "start_time", max: 64 }));
  sessions.fields.add(new TextField({ name: "end_time", max: 64 }));
  sessions.fields.add(new TextField({ name: "transcript", max: 200000 }));
  // created/updated are optional autodate fields as of PocketBase 0.23+.
  sessions.fields.add(new AutodateField({ name: "created", onCreate: true }));
  sessions.fields.add(new AutodateField({ name: "updated", onCreate: true, onUpdate: true }));
  sessions.indexes = ["CREATE UNIQUE INDEX idx_session_room ON sessions (room_name)"];
  app.save(sessions);
}, (app) => {
  for (const name of ["course_materials", "canvas_tokens", "canvas_data", "sessions"]) {
    app.delete(app.findCollectionByNameOrId(name));
  }
});
