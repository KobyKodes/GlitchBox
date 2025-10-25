<?php
////////////////////// SUPEREMBED PLAYER SCRIPT //////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////
////////////////////////// PLAYER SETTINGS - RETROFLIX THEME /////////////////////////////

// RetroFlix Retro Theme Customization
// Styled to match the retro terminal aesthetic of RetroFlix

// player font - VT323 for authentic retro terminal look
$player_font = "VT323";

// player colors - RetroFlix retro theme colors (HEX without #)
$player_bg_color = "0f0f1e"; // dark retro background
$player_font_color = "e0e0e0"; // light gray text
$player_primary_color = "00ff9f"; // retro green accent (matrix green)
$player_secondary_color = "ffd700"; // retro gold accent (midnight gold)

// player loader - retro animation
$player_loader = 3; // choose 1-10, picked 3 for retro feel

// preferred server - leave 0 for no preference
// options: vidlox=7, fembed=11, mixdrop=12, upstream=17, videobin=18,
// doodstream=21, streamtape=25, streamsb=26, voe=29, ninjastream=33
$preferred_server = 0;

// source list style - dropdown matches RetroFlix UI better
// 1 = button with server count and full page overlay
// 2 = button with icon and dropdown (recommended for RetroFlix)
$player_sources_toggle_type = 2;

//////////////////////////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////////////////////////////////////////////////

if(isset($_GET['video_id'])) {
    $video_id = $_GET['video_id'];
    $is_tmdb = 0;
    $season = 0;
    $episode = 0;
    $player_url = "";

    if(isset($_GET['tmdb'])) {
        $is_tmdb = $_GET['tmdb'];
    }

    if(isset($_GET['season'])) {
        $season = $_GET['season'];
    } else if (isset($_GET['s'])) {
        $season = $_GET['s'];
    }

    if(isset($_GET['episode'])) {
        $episode = $_GET['episode'];
    } else if(isset($_GET['e'])) {
        $episode = $_GET['e'];
    }

    if(!empty(trim($video_id))) {
        $request_url = "https://getsuperembed.link/?video_id=$video_id&tmdb=$is_tmdb&season=$season&episode=$episode&player_font=$player_font&player_bg_color=$player_bg_color&player_font_color=$player_font_color&player_primary_color=$player_primary_color&player_secondary_color=$player_secondary_color&player_loader=$player_loader&preferred_server=$preferred_server&player_sources_toggle_type=$player_sources_toggle_type";

        if(function_exists('curl_version')) {
            $curl = curl_init();
            curl_setopt($curl, CURLOPT_URL, $request_url);
            curl_setopt($curl, CURLOPT_RETURNTRANSFER, true);
            curl_setopt($curl, CURLOPT_FOLLOWLOCATION, true);
            curl_setopt($curl, CURLOPT_TIMEOUT, 7);
            curl_setopt($curl, CURLOPT_HEADER, false);
            curl_setopt($curl, CURLOPT_SSL_VERIFYPEER, FALSE);
            $player_url = curl_exec($curl);
            curl_close($curl);
        } else {
            $player_url = file_get_contents($request_url);
        }

        if(!empty($player_url)) {
            if(strpos($player_url, "https://") !== false) {
                header("Location: $player_url");
            } else {
                // RetroFlix styled error message
                echo '<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>RetroFlix - Player Error</title>
    <link href="https://fonts.googleapis.com/css2?family=VT323&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: "VT323", monospace;
            background: #0f0f1e;
            color: #e0e0e0;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
            font-size: 24px;
        }
        .error-box {
            border: 4px solid #00ff9f;
            padding: 40px;
            background: #1a1a2e;
            text-align: center;
            box-shadow: 0 0 20px rgba(0, 255, 159, 0.3);
        }
        .error-text {
            color: #ff6b6b;
            font-size: 28px;
            margin-bottom: 20px;
        }
        .error-details {
            color: #ffd700;
        }
    </style>
</head>
<body>
    <div class="error-box">
        <div class="error-text">⚠ PLAYER ERROR ⚠</div>
        <div class="error-details">' . htmlspecialchars($player_url) . '</div>
    </div>
</body>
</html>';
            }
        } else {
            // RetroFlix styled "no response" error
            echo '<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>RetroFlix - Server Error</title>
    <link href="https://fonts.googleapis.com/css2?family=VT323&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: "VT323", monospace;
            background: #0f0f1e;
            color: #e0e0e0;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
            font-size: 24px;
        }
        .error-box {
            border: 4px solid #00ff9f;
            padding: 40px;
            background: #1a1a2e;
            text-align: center;
            box-shadow: 0 0 20px rgba(0, 255, 159, 0.3);
        }
        .error-text {
            color: #ff6b6b;
            font-size: 28px;
            margin-bottom: 20px;
        }
        .error-details {
            color: #ffd700;
        }
    </style>
</head>
<body>
    <div class="error-box">
        <div class="error-text">⚠ SERVER ERROR ⚠</div>
        <div class="error-details">Request server didn\'t respond</div>
        <div style="margin-top: 20px; color: #00ff9f;">Please try again later</div>
    </div>
</body>
</html>';
        }
    } else {
        // RetroFlix styled "missing video_id" error
        echo '<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>RetroFlix - Missing Video ID</title>
    <link href="https://fonts.googleapis.com/css2?family=VT323&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: "VT323", monospace;
            background: #0f0f1e;
            color: #e0e0e0;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
            font-size: 24px;
        }
        .error-box {
            border: 4px solid #00ff9f;
            padding: 40px;
            background: #1a1a2e;
            text-align: center;
            box-shadow: 0 0 20px rgba(0, 255, 159, 0.3);
        }
        .error-text {
            color: #ff6b6b;
            font-size: 28px;
            margin-bottom: 20px;
        }
        .error-details {
            color: #ffd700;
        }
    </style>
</head>
<body>
    <div class="error-box">
        <div class="error-text">⚠ ERROR ⚠</div>
        <div class="error-details">Missing video_id parameter</div>
    </div>
</body>
</html>';
    }
} else {
    // RetroFlix styled "missing video_id" error
    echo '<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>RetroFlix - Missing Video ID</title>
    <link href="https://fonts.googleapis.com/css2?family=VT323&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: "VT323", monospace;
            background: #0f0f1e;
            color: #e0e0e0;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
            font-size: 24px;
        }
        .error-box {
            border: 4px solid #00ff9f;
            padding: 40px;
            background: #1a1a2e;
            text-align: center;
            box-shadow: 0 0 20px rgba(0, 255, 159, 0.3);
        }
        .error-text {
            color: #ff6b6b;
            font-size: 28px;
            margin-bottom: 20px;
        }
        .error-details {
            color: #ffd700;
        }
    </style>
</head>
<body>
    <div class="error-box">
        <div class="error-text">⚠ ERROR ⚠</div>
        <div class="error-details">Missing video_id parameter</div>
    </div>
</body>
</html>';
}

?>
