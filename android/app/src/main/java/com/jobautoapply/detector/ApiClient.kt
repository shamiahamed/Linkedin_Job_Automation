package com.jobautoapply.detector

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

object ApiClient {

    private val executor = Executors.newSingleThreadExecutor()

    fun post(baseUrl: String, token: String, text: String, source: String, dedupeKey: String) {
        executor.execute {
            try {
                val target = baseUrl.trimEnd('/') + "/api/jobs/from-text"
                val conn = URL(target).openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.connectTimeout = 15_000
                conn.readTimeout = 30_000
                conn.setRequestProperty("Content-Type", "application/json")
                conn.setRequestProperty("X-API-Key", token)
                val body = JSONObject()
                    .put("text", text)
                    .put("source", source)
                    .toString()
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }

                val ok = conn.responseCode in 200..299
                val stream = if (ok) conn.inputStream else conn.errorStream
                val msg = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                conn.disconnect()
                android.util.Log.i(
                    "JobAutoDetector",
                    "sent=$ok dedupe=$dedupeKey body=${msg.take(300)}"
                )
            } catch (e: Exception) {
                android.util.Log.e("JobAutoDetector", "send failed: ${e.message}")
            }
        }
    }
}