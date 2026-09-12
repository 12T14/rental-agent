import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildSearchContext,
  candidateArea,
  candidateCoordinates,
  draftKeepsPlaceSelection,
  isResolvedLocationPayload,
  mergePlaceIntoDraft,
  normalizeLocationCandidates
} from './placeCandidates.js'

test('normalizes the safe candidate fields and numeric coordinates', () => {
  const candidates = normalizeLocationCandidates({
    candidates: [{
      candidate_ref: 'pc_test',
      name: ' 示例园区东区 ',
      formatted_address: '江苏省示例市示例区示例大道21号',
      city: '示例市',
      district: '示例区',
      lng: '119.97',
      lat: 31.71,
      ignored_raw_field: 'must-not-pass-through'
    }]
  })

  assert.equal(candidates.length, 1)
  assert.equal(candidates[0].name, '示例园区东区')
  assert.deepEqual(candidateCoordinates(candidates[0]), [119.97, 31.71])
  assert.equal('ignored_raw_field' in candidates[0], false)
})

test('keeps list-only candidates but rejects invalid coordinates for the map', () => {
  const [candidate] = normalizeLocationCandidates({
    candidates: [{ name: '无坐标地点', lng: 'not-a-number', lat: 100 }]
  })

  assert.equal(candidate.candidate_ref, 'place-1')
  assert.equal(candidateCoordinates(candidate), null)
})

test('deduplicates references and drops malformed candidate entries', () => {
  const candidates = normalizeLocationCandidates({
    candidates: [
      null,
      { candidate_ref: 'same', name: '地点 A', lng: 120, lat: 31 },
      { candidate_ref: 'same', name: '地点 A 重复项', lng: 121, lat: 32 },
      { formatted_address: '缺少名称' }
    ]
  })

  assert.deepEqual(candidates.map(candidate => candidate.name), ['地点 A'])
})

test('builds a whitelisted structured context for the next explicit message', () => {
  const candidate = {
    candidate_ref: 'pc_confirmed',
    name: '示例园区东区',
    formatted_address: '江苏省示例市示例区示例大道21号',
    city: '示例市',
    district: '示例区',
    adcode: '329902',
    lng: '119.97',
    lat: 31.71,
    ignored_raw_field: 'must-not-pass-through'
  }

  assert.equal(candidateArea(candidate), '示例市 · 示例区')
  assert.deepEqual(buildSearchContext(candidate), {
    confirmed_target: {
      candidate_ref: 'pc_confirmed',
      name: '示例园区东区',
      formatted_address: '江苏省示例市示例区示例大道21号',
      city: '示例市',
      district: '示例区',
      adcode: '329902',
      lng: 119.97,
      lat: 31.71
    }
  })
})

test('fills an empty chat draft with a natural place prefix', () => {
  assert.equal(
    mergePlaceIntoDraft('', { name: '示例园区东区' }),
    '我想在示例园区东区附近找房，'
  )
})

test('preserves existing requirements when filling a place into the draft', () => {
  assert.equal(
    mergePlaceIntoDraft('预算3000元，整租一室', { name: '示例园区东区' }),
    '我想在示例园区东区附近找房，预算3000元，整租一室'
  )
})

test('does not duplicate a place prefix and replaces a previously filled candidate', () => {
  const first = { name: '示例园区西区' }
  const second = { name: '示例园区东区' }
  const initialDraft = mergePlaceIntoDraft('预算3000元', first)

  assert.equal(mergePlaceIntoDraft(initialDraft, first, first), initialDraft)
  assert.equal(
    mergePlaceIntoDraft(initialDraft, second, first),
    '我想在示例园区东区附近找房，预算3000元'
  )
})

test('keeps structured selection only while the generated place prefix is intact', () => {
  const candidate = { name: '示例园区东区' }

  assert.equal(
    draftKeepsPlaceSelection('我想在示例园区东区附近找房，预算3000元', candidate),
    true
  )
  assert.equal(
    draftKeepsPlaceSelection('我想在示例园区西区附近找房，预算3000元', candidate),
    false
  )
  assert.equal(draftKeepsPlaceSelection('预算3000元', candidate), false)
})

test('only treats an explicit non-ambiguous resolved payload as auto-confirmed', () => {
  assert.equal(isResolvedLocationPayload({ status: 'resolved', requires_user_confirmation: false }), true)
  assert.equal(isResolvedLocationPayload({ status: 'resolved', requires_user_confirmation: true }), false)
  assert.equal(isResolvedLocationPayload({ status: 'candidates_ready', requires_user_confirmation: false }), false)
  assert.equal(isResolvedLocationPayload({ status: 'resolved' }), false)
})
