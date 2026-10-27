package eventengine

import "time"

// MonitoringState is what the dashboard shows for a camera. Only
// StateOperational may be rendered as green.
type MonitoringState string

const (
	StateOperational    MonitoringState = "FULLY_OPERATIONAL"
	StateCameraUnavail  MonitoringState = "CAMERA_UNAVAILABLE"
	StateVideoDegraded  MonitoringState = "VIDEO_DEGRADED"
	StateAIUnavailable  MonitoringState = "AI_UNAVAILABLE"
	StateNeverConnected MonitoringState = "NOT_CONNECTED"
	defaultStaleAfter                   = 3 * time.Second
)

// Analyzable reports whether rules may run on this camera.
func (s MonitoringState) Analyzable() bool { return s == StateOperational }

type cameraHealth struct {
	lastObs     time.Time
	lastQuality Quality
	aiAvailable bool
}

// stateAt derives the monitoring state from the latest observation. A camera
// whose last observation is older than staleAfter is unavailable even if that
// observation looked healthy: a frozen pipeline must never look green.
func (h *cameraHealth) stateAt(now time.Time, staleAfter time.Duration) MonitoringState {
	switch {
	case h.lastObs.IsZero():
		return StateNeverConnected
	case now.Sub(h.lastObs) > staleAfter:
		return StateCameraUnavail
	case !h.aiAvailable:
		return StateAIUnavailable
	case h.lastQuality.Degraded:
		return StateVideoDegraded
	default:
		return StateOperational
	}
}
