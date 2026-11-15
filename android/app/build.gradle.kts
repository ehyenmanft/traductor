import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.ehyenma.traductor"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.ehyenma.traductor"
        minSdk = 29                       // Android 10: captura del audio de otras apps
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    // Firma de la versión para repartir: clave y contraseñas FUERA del repositorio (público).
    // Ruta: variable FIRMA_PROPS o ~/android-dev/firma/firma.properties. Sin ella, release sale sin firmar.
    val firma = file(System.getenv("FIRMA_PROPS") ?: "${System.getProperty("user.home")}/android-dev/firma/firma.properties")
    signingConfigs {
        if (firma.exists()) create("release") {
            val p = Properties().apply { firma.inputStream().use { load(it) } }
            storeFile = file(p.getProperty("storeFile"))
            storePassword = p.getProperty("storePassword")
            keyAlias = p.getProperty("keyAlias")
            keyPassword = p.getProperty("keyPassword")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("com.squareup.okhttp3:okhttp:4.12.0")   // WebSocket wss:// con el servidor
}
