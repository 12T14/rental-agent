import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applyDetailResults,
  applyListingEnrichment,
  defaultPlatformStatuses,
  detailFactEntries,
  formatListingCommute,
  formatListingDistance,
  listingAddressLabel,
  listingHasAddress,
  listingLocationNotice,
  listingPlaceLabel,
  listingNumberLabel,
  recommendationLabel,
  locationStatusLabel,
  normalizeListings,
  normalizePlatformStatuses,
  visibleListings
} from './listingState.js'

test('numbers and explicit recommendations survive enrichment, sorting and history', () => {
  const initial = normalizeListings({ listings: [
    { id: 'a', title: 'A', detailUrl: 'https://nj.58.com/zufang/a.shtml', displayNumber: 8, filterStatus: 'passed', detailStatus: 'ok', recommendation: { reason: '预算内', caveat: '水电待核验' } },
    { id: 'b', title: 'B', displayNumber: 17 },
    { id: 'c', title: 'C', displayNumber: 25 }
  ] })
  const enriched = applyListingEnrichment(initial, { listings: [
    { id: 'c', lng: 119.9, lat: 31.6 }, { id: 'a', detailStatus: 'ok' }, { id: 'b' }
  ] })
  assert.deepEqual(enriched.map(item => item.displayNumber), [25, 8, 17])
  assert.equal(listingNumberLabel(enriched[1]), '#8')
  assert.equal(recommendationLabel(enriched[1]), 'Agent 推荐')
  const restored = normalizeListings(JSON.parse(JSON.stringify({ listings: enriched })))
  assert.deepEqual(restored.map(item => item.displayNumber), [25, 8, 17])
  assert.equal(restored.filter(item => item.recommendation).length, 1)
  assert.equal(normalizeListings({ listings: [{ id: 'a', recommendation: null }] }, initial)[0].recommendation, null)
  assert.equal(normalizeListings({ listings: [{ id: 'a', filterStatus: 'excluded' }] }, initial)[0].recommendation, null)
})

test('legacy numbers stay stable and a detail result never creates a recommendation', () => {
  const initial = normalizeListings({ listings: [
    { id: 'a', detailUrl: 'https://nj.58.com/zufang/a.shtml' }, { id: 'b' }
  ] })
  const sorted = normalizeListings({ listings: [{ id: 'b' }, { id: 'a' }] }, initial)
  assert.deepEqual(sorted.map(item => item.displayNumber), [2, 1])
  const detailed = applyDetailResults(sorted, { details: [{ url: initial[0].detailUrl, status: 'ok' }] })
  assert.equal(detailed[1].recommendation, null)
  const fresh = normalizeListings({ listings: [{ id: 'b' }, { id: 'a' }] })
  assert.deepEqual(fresh.map(item => item.displayNumber), [1, 2])
})

test('normalizes safe listing and platform events into the UI shape', () => {
  const platforms = normalizePlatformStatuses({
    platforms: [
      { key: '58', name: '伪造名称', status: 'ok', label: '已获取', count: 2 },
      { key: 'anjuke', status: 'partial', count: 1 }
    ]
  }, defaultPlatformStatuses())
  const listings = normalizeListings({
    listings: [{
      id: 'l1', platformKey: '58', platform: '58同城', title: '示例房源',
      rent: 1200, area: 45, room: '一室', listingType: '整租', community: '示例小区',
      detailUrl: 'https://example.58.com/zufang/l1.html?secret=drop',
      sourceUrl: 'https://attacker.example/not-allowed', tags: ['独立卫浴', '独立卫浴'],
      offline: true, detailStatus: 'pending'
    }]
  })

  assert.deepEqual(platforms.map(item => [item.key, item.name, item.count]), [
    ['fifty-eight', '58同城', 2], ['anjuke', '安居客', 1], ['fang', '房天下', 0]
  ])
  assert.equal(listings.length, 1)
  assert.equal(listings[0].detailUrl, 'https://example.58.com/zufang/l1.html')
  assert.equal(listings[0].sourceUrl, null)
  assert.equal(listings[0].listingType, '整租')
  assert.deepEqual(listings[0].tags, ['独立卫浴'])
  assert.equal(listings[0].offline, true)
})

test('merges explicit detail facts without exposing evidence or artifact fields', () => {
  const listings = normalizeListings({
    listings: [{
      id: 'l1', platform: '安居客', title: '列表标题', rent: 1000,
      detailUrl: 'https://example.zu.anjuke.com/fangyuan/l1'
    }]
  })
  const merged = applyDetailResults(listings, {
    details: [{
      url: 'https://example.zu.anjuke.com/fangyuan/l1?track=drop',
      status: 'ok', title: '详情标题',
      facts: { monthly_rent_cny: 1050, payment_rule: '押一付三', agency_fee: '需收服务费' },
      evidence: ['must never be rendered'], artifact_path: 'D:/private.json'
    }]
  })

  assert.equal(merged[0].title, '详情标题')
  assert.equal(merged[0].rent, 1050)
  assert.equal(merged[0].detailFacts.payment_rule, '押一付三')
  assert.equal(merged[0].detailStatus, 'ok')
  assert.equal('evidence' in merged[0], false)
  assert.deepEqual(detailFactEntries(merged[0].detailFacts).map(item => item.label), ['押付规则', '服务费'])
})

test('keeps detail location, facts, and saved state when enrichment arrives later', () => {
  const listings = normalizeListings({
    listings: [{
      id: 'l-enriched', platform: '58同城', title: '列表标题', rent: 1800,
      community: '示例小区', address: '平台列表未提供具体地址',
      detailUrl: 'https://example.58.com/zufang/enriched.html',
      detailFacts: { payment_rule: '押一付一' },
      detailLocation: { community: '示例小区', address: '示例市示例区示例路 8 号', confidence: 'detail' },
      saved: true, detailStatus: 'ok'
    }]
  })
  listings[0].saved = true

  const enriched = applyListingEnrichment(listings, {
    status: 'ok',
    listings: [{
      id: 'l-enriched', platformKey: '58', title: '增强后的标题',
      lng: 120.1, lat: 31.9, locationStatus: 'verified',
      distanceMeters: 850, commuteValue: 18, commuteStatus: 'ok', commute: '约 18 分钟',
      rankingScore: 116, rankingReasons: ['满足目前已知硬条件']
    }]
  })

  assert.equal(enriched[0].title, '增强后的标题')
  assert.equal(enriched[0].locationStatus, 'verified')
  assert.equal(enriched[0].distance, 0.85)
  assert.equal(enriched[0].detailLocation.address, '示例市示例区示例路 8 号')
  assert.equal(enriched[0].detailFacts.payment_rule, '押一付一')
  assert.equal(enriched[0].saved, true)
  assert.equal(enriched[0].rankingReasons[0], '满足目前已知硬条件')
})

test('reads legacy snake-case listings and preserves the list-page boundary', () => {
  const previous = [{ id: 'legacy-1', saved: true }]
  const listings = normalizeListings({
    listings: [{
      listing_id: 'legacy-1', platform_key: 'fang', title: '列表候选', price: '1700',
      area_sqm: '45', url: 'https://example.zu.fang.com/house/wujin', url_type: 'detail',
      location_status: 'verified', distance: 1.2, commute_value: 28,
      tags: '近地铁, 精装'
    }]
  }, previous)

  assert.equal(listings.length, 1)
  assert.equal(listings[0].urlType, 'source_list')
  assert.equal(listings[0].detailUrl, null)
  assert.equal(listings[0].sourceUrl, 'https://example.zu.fang.com/house/wujin')
  assert.equal(listings[0].detailStatus, 'not_requested')
  assert.equal(listings[0].locationStatus, 'verified')
  assert.equal(listings[0].commuteValue, 28)
  assert.deepEqual(listings[0].tags, ['近地铁', '精装'])
  assert.equal(listings[0].saved, true)
})

test('keeps canonical Fang detail URLs distinct from city and region list pages', () => {
  const detailUrl = 'https://zu.fang.com/cz/chuzu/3_511256765_1.htm'
  const sourceUrl = 'https://zu.fang.com/cz/house-a0342/'
  const [listing] = normalizeListings({ listings: [{
    id: 'fang-detail', platform: '房天下', title: '公开房源', rent: 2000,
    detailUrl, sourceUrl, urlType: 'detail'
  }] })
  assert.equal(listing.detailUrl, detailUrl)
  assert.equal(listing.sourceUrl, sourceUrl)
  assert.equal(listing.urlType, 'detail')
  const [withDetail] = applyDetailResults([listing], { details: [{
    url: detailUrl, status: 'ok', facts: { payment_rule: '押一付一' }
  }] })
  assert.equal(withDetail.detailStatus, 'ok')
  assert.equal(withDetail.detailFacts.payment_rule, '押一付一')

  for (const url of ['https://zu.fang.com/cz/house/', sourceUrl, 'https://zu.fang.com/cz/hezu/']) {
    const [listOnly] = normalizeListings({ listings: [{
      id: 'fang-list', platform: '房天下', detailUrl: url, urlType: 'detail'
    }] })
    assert.equal(listOnly.detailUrl, null)
    assert.equal(listOnly.urlType, 'source_list')
  }
})

test('preserves region evidence through details, enrichment and history without verifying proximity', () => {
  const [listing] = normalizeListings({ listings: [{
    id: 'fang-region', platform: '房天下', title: '区域房源',
    detail_url: 'https://zu.fang.com/cz/chuzu/3_1_1.htm',
    region_scope: 'administrative_region', region_scope_name: '武进', region_evidence: 'official_filter_page'
  }] })
  assert.equal(listing.regionScope, 'administrative_region')
  assert.equal(listing.regionScopeName, '武进')
  assert.equal(listing.regionEvidence, 'official_filter_page')
  assert.equal(listing.locationStatus, 'unverified')
  assert.equal(listing.distance, null)
  assert.match(listingLocationNotice(listing), /武进官方行政区筛选页/)
  assert.match(listingLocationNotice(listing), /不能仅据此认定为附近/)
  const merged = applyDetailResults([listing], { details: [{
    url: listing.detailUrl, status: 'ok', location: { address: '测试路8号' }
  }] })
  const enriched = applyListingEnrichment(merged, { listings: [{
    id: listing.id, geocodeStatus: 'missing_address'
  }] })
  const [restored] = normalizeListings({ listings: JSON.parse(JSON.stringify(enriched)) })
  assert.equal(restored.regionEvidence, 'official_filter_page')
  assert.equal(restored.regionScopeName, '武进')
  assert.equal(restored.regionScope, 'administrative_region')
  assert.equal(restored.address, '测试路8号')
  assert.match(listingLocationNotice(restored), /仅有区域范围依据/)
  assert.equal(restored.locationStatus, 'unverified')
  const [verified] = applyListingEnrichment([restored], { listings: [{
    id: listing.id, lng: 119.9, lat: 31.7, locationStatus: 'verified',
    geocodeStatus: 'ok', distanceMeters: 900
  }] })
  assert.equal(verified.regionEvidence, 'official_filter_page')
  assert.equal(verified.distance, 0.9)
  assert.doesNotMatch(listingLocationNotice(verified), /仅有区域范围依据/)
})

test('bounds region names and rejects unknown region evidence', () => {
  const [listing] = normalizeListings({ listings: [{
    id: 'bad-region', title: '测试', regionScope: 'verified_nearby',
    regionScopeName: 'x'.repeat(150), regionEvidence: { raw_html: 'private' }
  }] })
  assert.equal(listing.regionScope, '')
  assert.equal(listing.regionEvidence, '')
  assert.equal(listing.regionScopeName.length, 100)
})

test('labels missing platform location data without presenting it as a verified address', () => {
  const [listing] = normalizeListings({
    listings: [{
      id: 'missing-location', title: '标题线索',
      community: '未说明', address: '平台列表未提供具体地址'
    }]
  })

  assert.equal(listingPlaceLabel(listing), '地址未提供')
  assert.equal(listingAddressLabel(listing), '平台列表未提供具体地址')
  assert.equal(listingHasAddress(listing), false)
})

test('distinguishes a geocoded listing from a still-unconfirmed target', () => {
  const [listing] = normalizeListings({
    listings: [{
      id: 'target-pending', title: '已定位房源',
      address: '江苏省示例市示例区示例路 168 号',
      lng: 120.282, lat: 31.982,
      geocodeStatus: 'ok', commuteStatus: 'target_pending'
    }]
  })

  assert.equal(locationStatusLabel(listing), '房源地址已定位')
  assert.equal(formatListingDistance(listing), '目标地点待确认')
  assert.equal(formatListingCommute(listing), '目标地点待确认')
  assert.equal(listingLocationNotice(listing), '房源地址已独立定位；确认目标地点后，才会计算距离和通勤。')
})

test('labels a cross-city geocode as unverified instead of showing stale metrics', () => {
  const [listing] = normalizeListings({
    listings: [{
      id: 'city-conflict', title: '城市冲突房源',
      address: '天津市南开区某路 1 号',
      lng: 117.2, lat: 39.1,
      geocodeStatus: 'city_conflict', commuteStatus: 'city_conflict',
      locationStatus: 'verified', distance: 1.2, commute: '约 10 分钟'
    }]
  })

  assert.equal(listing.locationStatus, 'unverified')
  assert.equal(listing.lng, null)
  assert.equal(listing.lat, null)
  assert.equal(listing.distance, null)
  assert.equal(listing.commute, '位置待核验')
  assert.equal(locationStatusLabel(listing), '城市信息冲突')
  assert.equal(formatListingDistance(listing), '位置待核验')
  assert.equal(formatListingCommute(listing), '位置待核验')
  assert.equal(listingLocationNotice(listing), '平台地址与目标城市信息冲突，未用于距离和通勤计算。')
})

test('preserves details skipped after the first platform block', () => {
  const listings = normalizeListings({
    listings: [{
      id: 'l1', title: '列表候选',
      detailUrl: 'https://example.58.com/zufang/l1.html'
    }]
  })
  const [merged] = applyDetailResults(listings, {
    details: [{
      url: 'https://example.58.com/zufang/l1.html',
      status: 'skipped_after_block'
    }]
  })

  assert.equal(merged.detailStatus, 'skipped_after_block')
})

test('shows candidates that do not violate hard filters', () => {
  const visible = visibleListings([
    { id: 'passed', filterStatus: 'passed' },
    { id: 'unknown', filterStatus: 'unknown' },
    { id: 'pending' },
    { id: 'excluded', filterStatus: 'excluded' }
  ])

  assert.deepEqual(visible.map(item => item.id), ['passed', 'unknown', 'pending'])
})
