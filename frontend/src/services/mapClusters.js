// Group overlapping screen-space markers without dropping any listing. Use
// the map's projection so zoom, rotation and viewport changes stay accurate.
export function groupListingPoints(points, project, radius = 44) {
  const groups = []
  for (const point of points) {
    const pixel = project(point.position)
    const x = typeof pixel?.getX === 'function' ? pixel.getX() : pixel?.x
    const y = typeof pixel?.getY === 'function' ? pixel.getY() : pixel?.y
    const entry = { ...point, pixel: { x, y } }
    const overlaps = other => (
      point.position[0] === other.position[0] && point.position[1] === other.position[1]
    ) || (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(other.pixel.x)
      && Number.isFinite(other.pixel.y) && Math.hypot(x - other.pixel.x, y - other.pixel.y) < radius)
    const matching = groups.filter(group => group.some(overlaps))
    if (!matching.length) groups.push([entry])
    else {
      matching[0].push(entry)
      for (const group of matching.slice(1)) {
        matching[0].push(...group)
        groups.splice(groups.indexOf(group), 1)
      }
    }
  }
  return groups.map(group => ({
    position: group[0].position,
    listings: group.map(point => point.listing),
    count: group.length,
    recommended: group.some(point => (
      point.listing?.filterStatus === 'passed'
      && point.listing?.detailStatus === 'ok'
      && Boolean(point.listing?.recommendation)
    )),
    qualified: group.some(point => point.listing?.filterStatus === 'passed'),
    detailPending: group.some(point => point.listing?.filterStatus === 'passed' && point.listing?.detailStatus !== 'ok')
  }))
}
