import assert from 'node:assert/strict'
import test from 'node:test'
import { groupListingPoints } from './mapClusters.js'

const point = (id, x, y, recommended = false) => ({
  position: [x, y],
  listing: {
    id,
    filterStatus: 'passed',
    detailStatus: recommended ? 'ok' : 'pending',
    recommendation: recommended ? { reason: 'test' } : null
  }
})
const project = ([x, y]) => ({ x, y })

test('overlapping markers account for every house and preserve recommendation membership', () => {
  const points = [point('a', 0, 0), point('b', 0, 0, true), point('c', 10, 10), point('d', 100, 100)]
  const groups = groupListingPoints(points, project)
  assert.deepEqual(groups.map(group => group.count), [3, 1])
  assert.equal(groups[0].recommended, true)
  assert.deepEqual(groups.flatMap(group => group.listings).map(item => item.id).sort(), ['a', 'b', 'c', 'd'])
})

test('zoom separates close houses but identical coordinates remain an explorable group', () => {
  const points = [point('a', 0, 0), point('b', 0, 0), point('c', 10, 10)]
  const zoomed = groupListingPoints(points, ([x, y]) => ({ getX: () => x * 10, getY: () => y * 10 }))
  assert.deepEqual(zoomed.map(group => group.count), [2, 1])
})
