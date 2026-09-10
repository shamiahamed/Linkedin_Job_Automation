package com.jobautoapply.detector

import android.accessibilityservice.AccessibilityService
import android.content.SharedPreferences
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

class DetectorService : AccessibilityService() {

    private lateinit var prefs: SharedPreferences

    private val postUrlRe = Regex("https://(www\\.)?linkedin\\.com/posts/[\\w%./?=-]+")
    private val jobHintRe = Regex(
        "(?i)(hiring|vacancies|openings|we.ve an opening|apply|internship|job|opportunity|" +
            "fresher|graduate|role|looking for|requirement|experience|salary|remote|location)"
    )

    override fun onServiceConnected() {
        super.onServiceConnected()
        prefs = getSharedPreferences("prefs", MODE_PRIVATE)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (!prefs.getBoolean("enabled", true)) return
        if (prefs.getString("url", null).isNullOrBlank()) return
        if (prefs.getString("token", null).isNullOrBlank()) return

        val root = try {
            rootInActiveWindow
        } catch (e: Exception) {
            null
        } ?: return

        val text = dump(root)
        if (text.length < 120) return
        if (!isJobLike(text)) return

        val url = postUrlRe.find(text)?.value ?: ""
        val keyBase = if (url.isNotEmpty()) url else text.trim().take(90)
        val dedupeKey = "sent_${keyBase.hashCode().toString().replace("-", "n")}"
        if (prefs.getBoolean(dedupeKey, false)) return

        prefs.edit().putBoolean(dedupeKey, true).apply()
        ApiClient.post(
            baseUrl = prefs.getString("url", "") ?: "",
            token = prefs.getString("token", "") ?: "",
            text = text,
            source = "android_accessibility",
            dedupeKey = dedupeKey
        )
    }

    private fun isJobLike(text: String): Boolean {
        val low = text.lowercase()
        val hints = jobHintRe.findAll(low).count()
        if (hints >= 3) return true
        if (text.length >= 300 && ("hiring" in low || "apply" in low)) return true
        return false
    }

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

    override fun onInterrupt() {}
}