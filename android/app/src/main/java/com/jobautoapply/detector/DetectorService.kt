package com.jobautoapply.detector

import android.accessibilityservice.AccessibilityService
import android.content.SharedPreferences
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * COLLECTOR ONLY. This service does NOT decide whether on-screen text is a job.
 * Its job (and nothing else):
 *   1. notice meaningful LinkedIn UI/content changes,
 *   2. extract the raw visible text,
 *   3. debounce the flood of accessibility events,
 *   4. lightly normalize the text (whitespace, chrome lines are left as-is),
 *   5. ship the candidate text to the backend, and
 *   6. skip content we already sent (local dedupe).
 *
 * Whether the text is actually a job post is decided by the backend's Groq
 * pipeline (/api/jobs/from-text). Candidates the Groq model rejects come back as
 * 422 and are simply ignored here, as designed.
 */
class DetectorService : AccessibilityService() {

    private lateinit var prefs: SharedPreferences
    private val handler = Handler(Looper.getMainLooper())

    private var liveText = ""          // latest normalized screen snapshot
    private var lastSentMarker = ""    // marker of content already posted

    private val linkedinUrlRe = Regex("https://(www\\.)?linkedin\\.com/[\\w%./?=#&+-]+")

    override fun onServiceConnected() {
        super.onServiceConnected()
        prefs = getSharedPreferences("prefs", MODE_PRIVATE)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (!prefs.getBoolean("enabled", true)) return
        val base = prefs.getString("url", null)
        val token = prefs.getString("token", null)
        if (base.isNullOrBlank() || token.isNullOrBlank()) return

        // Only meaningful content/window events matter; this service is already
        // package-scoped to com.linkedin.android, so nothing else can reach it.
        if (event.eventType != AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED &&
            event.eventType != AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED &&
            event.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            return
        }

        val root = try {
            rootInActiveWindow
        } catch (e: Exception) {
            null
        } ?: return

        val text = normalize(dump(root))
        // Sanity floor only (a practically empty screen is not worth a request).
        // This is NOT a job-content classifier — that belongs to Groq/backend.
        if (text.length < MIN_TEXT_BYTES) return
        if (text == liveText) return // screen unchanged since last poll

        liveText = text
        // Debounce: keep restarting until the user stops scrolling for a beat,
        // then send the freshest snapshot once.
        handler.removeCallbacks(sendRunnable)
        handler.postDelayed(sendRunnable, DEBOUNCE_MS)
    }

    private val sendRunnable = Runnable { maybeSend() }

    private fun maybeSend() {
        val text = liveText
        if (text.length < MIN_TEXT_BYTES) return

        val marker = markerFor(text)
        if (marker.isEmpty()) return
        if (marker == lastSentMarker) return // already sent this content

        lastSentMarker = marker
        ApiClient.post(
            baseUrl = prefs.getString("url", "") ?: "",
            token = prefs.getString("token", "") ?: "",
            text = text,
            source = "android_accessibility",
            dedupeKey = "a_${marker.hashCode().toString().replace("-", "n")}"
        )
    }

    /** Stable identity for local dedupe: a LinkedIn post URL if present, else the
     *  opening snippet of the snapshot. Local dedupe only — not job classification. */
    private fun markerFor(text: String): String {
        val url = linkedinUrlRe.find(text)?.value ?: ""
        return if (url.isNotEmpty()) url else text.take(90)
    }

    /** Light normalization: trim every line, drop empties, keep everything else
     *  verbatim so Groq sees the full candidate text. */
    private fun normalize(raw: String): String =
        raw
            .split('\n')
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .joinToString("\n")
            .trim()

    private fun dump(node: AccessibilityNodeInfo?, out: StringBuilder = StringBuilder()): String {
        if (node == null) return out.toString()
        try {
            val t = node.text?.toString()?.trim()
            if (!t.isNullOrEmpty()) {
                out.append(t).append('\n')
            }
        } catch (e: Exception) {
            // stale node, skip
        }
        for (i in 0 until node.childCount) {
            val child = try {
                node.getChild(i)
            } catch (e: Exception) {
                null
            }
            dump(child, out)
        }
        return out.toString()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    override fun onInterrupt() {}

    companion object {
        private const val DEBOUNCE_MS = 2500L
        private const val MIN_TEXT_BYTES = 120
    }
}