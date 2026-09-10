package com.jobautoapply.detector

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast

class MainActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences("prefs", Context.MODE_PRIVATE)

        val title = textView("Job Auto-Detector", 20, bold = true)
        val info = textView(
            "Scroll the LinkedIn app while this is enabled; job posts are detected " +
                "from the screen text and sent to your Job Auto-Apply backend automatically.", 14
        )
        info.setPadding(0, 0, 0, 32)

        val urlLabel = textView("Backend URL", 14, bold = true)
        val urlInput = EditText(this).apply {
            hint = "https://linkedin-job-automation-abhh.onrender.com"
            setText(prefs.getString("url", "") ?: "")
        }

        val tokenLabel = textView("API token", 14, bold = true)
        val tokenInput = EditText(this).apply {
            hint = "paste your API_TOKEN"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setText(prefs.getString("token", "") ?: "")
        }

        val enabledSwitch = Switch(this).apply {
            text = "Detection enabled"
            isChecked = prefs.getBoolean("enabled", true)
        }

        val saveButton = Button(this).apply {
            text = "Save and open Accessibility settings"
            setOnClickListener {
                val url = urlInput.text.toString().trim()
                val token = tokenInput.text.toString().trim()
                if (url.isEmpty() || token.isEmpty()) {
                    Toast.makeText(this@MainActivity, "Backend URL and token are required",
                        Toast.LENGTH_LONG).show()
                    return@setOnClickListener
                }
                prefs.edit()
                    .putString("url", url)
                    .putString("token", token)
                    .putBoolean("enabled", enabledSwitch.isChecked)
                    .apply()
                Toast.makeText(this@MainActivity, "Saved", Toast.LENGTH_SHORT).show()
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        }

        val testButton = Button(this).apply {
            text = "Test backend connection"
            setOnClickListener {
                val url = urlInput.text.toString().trim()
                val token = tokenInput.text.toString().trim()
                if (url.isEmpty() || token.isEmpty()) {
                    Toast.makeText(this@MainActivity, "Enter URL and token first",
                        Toast.LENGTH_LONG).show()
                    return@setOnClickListener
                }
                testConnection(url, token)
            }
        }

        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 48, 48, 48)
            addView(title)
            addView(info)
            addView(urlLabel)
            addView(urlInput)
            addView(tokenLabel)
            addView(tokenInput)
            addView(enabledSwitch)
            addView(saveButton)
            addView(testButton)
        }
        setContentView(layout)
    }

    private fun testConnection(url: String, token: String) {
        ApiClient.post(
            baseUrl = url,
            token = token,
            text = "Connection test from detached detector app",
            source = "android_connection_test",
            dedupeKey = "test_${System.currentTimeMillis()}"
        )
        Toast.makeText(this, "Test posted — check your dashboard", Toast.LENGTH_LONG).show()
    }

    private fun textView(text: String, size: Int, bold: Boolean = false): TextView =
        TextView(this).apply {
            this.text = text
            textSize = size.toFloat()
            typeface = if (bold) android.graphics.Typeface.DEFAULT_BOLD else android.graphics.Typeface.DEFAULT
            setPadding(0, 0, 0, 8)
        }
}