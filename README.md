# Web VNC V(1.1)
- Web VNC is a browser-based remote viewing and control tool that streams the host desktop over websockets and provides a clean web client for connecting from desktop or mobile. It supports multiple view connections at the same time, with a single control session, plus multi-monitor support and a login-based access model.

## Feedback
- We hope you find Web VNC helpful and easy to use. If you have any thoughts or suggestions, we’d love to hear from you at feedback@nucleonautomation.com. Your feedback is invaluable in helping us improve and enhance the tool.

## Features
- Web Browser Client: Connect from any modern browser with websocket streaming and input control.
- Multi View Sessions: Multiple clients can view simultaneously with one active control session.
- Multi Monitor Support: Monitor selection and streaming for multi-display systems.
- Mouse Control: Normalized client coordinates mapped to host screen space with click and drag support.
- Keyboard Support: Key input and key combo support for common shortcuts and workflows.
- Client Tools: Copy, paste, cut, undo, fullscreen toggle, and scale modes including fit, 1:1, and stretched.
- User Permissions: User management with per-user control permission.
- Secure Login Flow: Login-based access with hashed password exchange.
- Built In Servers: Threaded HTTP server plus websocket server for streaming and control.
- Optional HTTPS: Optional HTTPS support for serving the web client.

## Installation Git
```
# Requires Python >=3.6
git clone https://github.com/nucleonautomation/Web-VNC.git
```

## Requirements
```
pip install -r requirements.txt
```

## Quick Start
```
#Import Libs
from VNC import VNC

#Server Setup
Server = VNC(IP="", Port=8080, VNC_Port=5900, Web_Root="Localhost", Capture_Interval=0.02)

#Add Users
Server.Add("viewer", "viewerpass", False)
Server.Add("operator", "operatorpass", True)

#Server Start
Server.Start()
```

## Quick Start Notes
- IP: The bind address for the HTTP server. Use "" to bind on all interfaces, or a specific address like "127.0.0.1" for local only.
- Port: The HTTP port that serves the web client files in your browser, for example http://IP:8080.
- VNC Port: The websocket port used for streaming frames and receiving mouse and keyboard events from the browser client.
- Web Root: The folder that contains the web client files to be served, such as index.html, css, and js.
- Capture Interval: Frame capture interval in seconds. Lower values increase fps and cpu usage, higher values reduce load.
- Server.Add(Username, Password, Control):
  - Username: Login username.
  - Password: Login password.
  - Control: If True, this user can request control of mouse and keyboard. If False, the user can only view.

## License
- This project is licensed under BSD 4-Clause License. See the [LICENSE](https://github.com/nucleonautomation/Web-VNC/blob/main/LICENSE.md) file for details.

## Change Log
- See the [LOG](https://github.com/nucleonautomation/Web-VNC/blob/main/LOG.md) file for details.