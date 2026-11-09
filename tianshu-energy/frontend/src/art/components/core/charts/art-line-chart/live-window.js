// Live interaction options for the existing ArtLineChart, not a new chart renderer.
export const clampWindow = seconds => Math.max(15, Math.min(1800, Math.round(seconds)))
export function liveZoom(latest, seconds = 180) {
 return {id:'live-time-window',type:'inside',xAxisIndex:0,filterMode:'filter',rangeMode:['value','value'],startValue:latest-clampWindow(seconds)*1000,endValue:latest,minValueSpan:15000,maxValueSpan:1800000,zoomOnMouseWheel:true,moveOnMouseWheel:false,moveOnMouseMove:false,throttle:50}
}

// Compare primitive values, not Vue proxies or freshly allocated prop arrays.
// Retain all samples for wheel zoom; ECharts filters off-screen samples before drawing.
export function createLiveUpdater() {
 let previous = null
 let previousWindow = null
 return (timestamps, values, seconds = 180) => {
  const window = clampWindow(seconds)
  const same = previous && previous.length === values.length && values.every((v, i) =>
   Object.is(v, previous[i][1]) && timestamps[i] * 1000 === previous[i][0])
  if (same && window === previousWindow) return null
  const points = same ? previous : values.map((v, i) => [timestamps[i] * 1000, v])
  const latest = points.at(-1)?.[0]
  if (!Number.isFinite(latest)) return null
  previous = points
  previousWindow = window
  return {
   animation: false,
   xAxis: {min: latest - 1800000, max: latest},
   dataZoom: [liveZoom(latest, window)],
   series: [{id: 'live-power', type: 'line', animation: false, data: points}]
  }
 }
}
export function zoomSeconds(zoom) {
 const seconds=(Number(zoom?.endValue)-Number(zoom?.startValue))/1000
 return Number.isFinite(seconds)&&seconds>0?clampWindow(seconds):null
}
