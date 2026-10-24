const test = require('node:test')
const assert = require('node:assert/strict')
const { loadMain } = require('../zernio/support/load-main.cjs')
const fixture = require('../fixtures/editor/project.json')
const schema = loadMain("export * from './src/shared/clip-editor'; export * from './src/renderer/lib/caption-preview'")

test('caption position is validated, saved in render edits and reset without changing review evidence', () => {
  const old = schema.parseEditorProject(structuredClone(fixture)).candidates[0]
  assert.equal(old.caption_y, null)
  for (const y of [NaN, Infinity, -.1, .09, .91, true, '0.5', {}]) {
    assert.throws(() => schema.parseCandidateEdit({ ...old, caption_y: y }, fixture.duration_ms))
  }
  const ready = { ...old, status: 'ready' }
  const placed = schema.refineEdit(ready, { caption_y: .25 })
  assert.equal(placed.status, 'ready')
  assert.equal(schema.candidateEdit(placed).caption_y, .25)
  assert.equal(schema.editSignature(placed), schema.editSignature(ready))
  assert.notEqual(schema.renderEditKey(placed), schema.renderEditKey(ready))
  const reset = schema.parseCandidateEdit(schema.candidateEdit({ ...placed, caption_y: null }), fixture.duration_ms)
  assert.equal(reset.caption_y, null)
  // Title header: saved only when on, changes the render, rejects non-booleans.
  assert.equal('title_card' in schema.candidateEdit(old), false)
  const titled = schema.refineEdit(ready, { title_card: true })
  assert.equal(schema.parseCandidateEdit(schema.candidateEdit(titled), fixture.duration_ms).title_card, true)
  assert.notEqual(schema.renderEditKey(titled), schema.renderEditKey(ready))
  assert.throws(() => schema.parseCandidateEdit({ ...old, title_card: 'yes' }, fixture.duration_ms))
  const placedTitle = schema.parseCandidateEdit(schema.candidateEdit({ ...titled, title_y: .3, title_scale: 1.5, caption_scale: .75 }), fixture.duration_ms)
  assert.equal(placedTitle.title_y, .3); assert.equal(placedTitle.title_scale, 1.5); assert.equal(placedTitle.caption_scale, .75)
  assert.equal(schema.parseCandidateEdit({ ...old, caption_color: '#ff0000' }, fixture.duration_ms).caption_color, '#FF0000')
  assert.equal(schema.candidateEdit({ ...old, caption_color: undefined }).caption_color, undefined)
  for (const bad of [{ caption_color: 'red' }, { caption_highlight: '#12345' }, { caption_box: 5 }]) {
    assert.throws(() => schema.parseCandidateEdit({ ...old, ...bad }, fixture.duration_ms))
  }
  for (const bad of [{ title_y: .01 }, { title_y: 'top' }, { title_scale: 3 }, { title_scale: true }, { caption_scale: .2 }, { caption_scale: '2' }]) {
    assert.throws(() => schema.parseCandidateEdit({ ...old, ...bad }, fixture.duration_ms))
  }
})

test('automatic caption anchors follow portrait layouts and landscape; manual placement overrides them', () => {
  const project = schema.parseEditorProject(structuredClone(fixture)), c = project.candidates[0]
  c.scenes = [{ at_ms: 0, layout: 'fill', crops: [[0, 0, 1, 1]] }]
  assert.deepEqual(schema.captionAnchor(project, c, 2000), { y: 1340 / 1920, bottom: false })
  c.scenes[0].layout = 'split'
  assert.deepEqual(schema.captionAnchor(project, c, 2000), { y: .5, bottom: false })
  c.scenes[0].layout = 'fit'
  assert.equal(schema.captionAnchor(project, c, 2000).bottom, true)
  project.aspect_ratio = '16:9'
  assert.deepEqual(schema.captionAnchor(project, c, 2000), { y: 1 - 100 / 1080, bottom: true })
  c.caption_y = .2
  assert.deepEqual(schema.captionAnchor(project, c, 2000), { y: .2, bottom: false })
})
