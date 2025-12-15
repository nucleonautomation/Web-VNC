import os
import time
import json
import shutil
import socket
import hashlib
import threading
import ssl
import select
import base64
import struct
import urllib.request
import urllib.parse
from http.server import CGIHTTPRequestHandler
from http.server import ThreadingHTTPServer
import mss
import mss.tools
import pyautogui

class VNC:
    
    def __init__(self, IP='', Port=8080, VNC_Port=5900, Web_Root="Localhost", Capture_Interval=0.02, Callback=None):
        self._IP = IP
        self._Port = Port
        self._VNC_Port = VNC_Port
        self._Capture_Interval = Capture_Interval
        self._Web_Root = Web_Root
        self._Http_Server = HTTP.Server(self._Port, IP=self._IP)
        self._Socket = HTTP.Socket(self._VNC_Port, IP=self._IP)
        self._Running = False
        self._Http_Thread = None
        self._Receive_Thread = None
        self._Capture_Thread = None
        self._Mouse_Thread = None
        self._Mouse_Lock = threading.Lock()
        self._Mouse_Has_State = False
        self._Mouse_Down = False
        self._Mouse_Button = "left"
        self._Mouse_X = 0.0
        self._Mouse_Y = 0.0
        self._Mouse_Prev_Down = False
        self._Mouse_Prev_X = None
        self._Mouse_Prev_Y = None
        self._Monitor_Lock = threading.Lock()
        self._Monitor_Count = 1
        self._Active_Monitor_Index = 1
        try:
            self._Screen_Size = pyautogui.size()
        except Exception:
            self._Screen_Size = None
        self._Users = {}
        self._Clients_Info = {}
        self._Controller_User_Key = None
        self._Controller_User = None
        self._Callback = Callback

    def Start(self):
        self._Prepare_Web_Root()
        self._Http_Server.Root(self._Web_Root)
        self._Http_Server.CORS("*")
        try:
            with mss.mss() as Screen_Capture:
                Monitors = Screen_Capture.monitors
                if len(Monitors) > 1:
                    Count = len(Monitors) - 1
                else:
                    Count = 1
        except Exception:
            Count = 1
        with self._Monitor_Lock:
            self._Monitor_Count = Count
            if self._Active_Monitor_Index < 1 or self._Active_Monitor_Index > Count:
                self._Active_Monitor_Index = 1
        self._Http_Thread = threading.Thread(target=self._Http_Server.Start, daemon=True)
        self._Http_Thread.start()
        self._Socket.Start()
        self._Running = True
        self._Receive_Thread = threading.Thread(target=self._Receive_Loop, daemon=True)
        self._Receive_Thread.start()
        self._Capture_Thread = threading.Thread(target=self._Capture_Loop, daemon=True)
        self._Capture_Thread.start()
        self._Mouse_Thread = threading.Thread(target=self._Mouse_Loop, daemon=True)
        self._Mouse_Thread.start()
        self._Run_Forever()

    def Stop(self):
        self._Running = False
        try:
            self._Http_Server.Close()
        except Exception:
            pass
        try:
            self._Socket.Close()
        except Exception:
            pass

    def Add(self, Username, Password, Control):
        Username_Text = str(Username or "").strip()
        if not Username_Text:
            return False
        Username_Key = Username_Text.lower()
        Password_Text = str(Password or "")
        Control_Flag = bool(Control)
        self._Users[Username_Key] = {"Password": Password_Text, "Control": Control_Flag}
        return True

    def Remove(self, Username):
        Username_Text = str(Username or "").strip()
        if not Username_Text:
            return False
        Username_Key = Username_Text.lower()
        if Username_Key in self._Users:
            del self._Users[Username_Key]
        Clients_To_Close = []
        for Client, Info in list(self._Clients_Info.items()):
            if Info.get("User_Key") == Username_Key:
                Clients_To_Close.append(Client)
                del self._Clients_Info[Client]
        for Client in Clients_To_Close:
            try:
                self._Socket.Close_Client(Client)
            except Exception:
                pass
        if self._Controller_User_Key == Username_Key:
            self._Controller_User_Key = None
            self._Controller_User = None
        return True

    def _Receive_Loop(self):
        Client = None
        while self._Running:
            try:
                Client, Payload = self._Socket.Receive(Client)
            except Exception:
                Client = None
                Payload = None
            if Client is None:
                continue
            if Payload is None:
                Info = self._Clients_Info.pop(Client, None)
                if Info:
                    User_Name = Info.get("User") or ""
                    User_Key = Info.get("User_Key") or ""
                    self._Emit_Event({"Event": "Disconnect", "User": User_Name, "User_Key": User_Key})
                    if Info.get("Has_Control"):
                        Controller_User_Key = Info.get("User_Key")
                        if Controller_User_Key and Controller_User_Key == self._Controller_User_Key:
                            Controller_Alive = False
                            for Client_Id, Client_Info in list(self._Clients_Info.items()):
                                if Client_Info.get("User_Key") == Controller_User_Key and Client_Info.get("Authenticated") and Client_Info.get("Control_Allowed") and Client_Info.get("Has_Control"):
                                    Controller_Alive = True
                                    break
                            if not Controller_Alive:
                                self._Controller_User_Key = None
                                self._Controller_User = None
                                self._Emit_Event({"Event": "Control_Change", "Type": "Disconnect", "User": User_Name, "User_Key": Controller_User_Key})
                Client = None
                continue
            try:
                Message = json.loads(Payload)
            except Exception:
                continue
            Message_Type = Message.get("Type")
            try:
                if Message_Type == "Login":
                    self._Handle_Login(Message, Client)
                elif Message_Type == "Control_Request":
                    self._Handle_Control_Request(Message, Client)
                elif Message_Type == "Control_Release":
                    self._Handle_Control_Release(Message, Client)
                elif Message_Type == "Click":
                    self._Handle_Click(Message, Client)
                elif Message_Type == "Monitor_Select":
                    self._Handle_Monitor_Select(Message, Client)
                elif Message_Type == "Hello":
                    self._Send_Monitor_Info(Client)
                elif Message_Type == "Key":
                    self._Handle_Key(Message, Client)
                elif Message_Type == "Key_Combo":
                    self._Handle_Key_Combo(Message, Client)
            except Exception:
                continue

    def _Emit_Event(self, Data):
        if self._Callback is None:
            return
        Event_Data = dict(Data)
        if "Timestamp" not in Event_Data:
            Event_Data["Timestamp"] = time.time()
        try:
            self._Callback(Event_Data)
        except Exception:
            pass

    def _Normalize_Key_Name(self, Key_Value):
        if Key_Value is None:
            return None
        Key_Text = str(Key_Value)
        Lower = Key_Text.lower()
        if len(Key_Text) == 1:
            return Lower
        if Lower == " ":
            return "space"
        if Lower == "spacebar":
            return "space"
        if Lower == "enter":
            return "enter"
        if Lower == "backspace":
            return "backspace"
        if Lower == "tab":
            return "tab"
        if Lower == "escape" or Lower == "esc":
            return "esc"
        if Lower == "shift" or Lower == "shiftleft" or Lower == "shiftright":
            return "shift"
        if Lower == "control" or Lower == "ctrl" or Lower == "controlleft" or Lower == "controlright":
            return "ctrl"
        if Lower == "alt" or Lower == "altleft" or Lower == "altright":
            return "alt"
        if Lower == "meta" or Lower == "metaleft" or Lower == "metaright" or Lower == "win" or Lower == "super" or Lower == "os" or Lower == "command" or Lower == "cmd":
            return "win"
        if Lower == "arrowup":
            return "up"
        if Lower == "arrowdown":
            return "down"
        if Lower == "arrowleft":
            return "left"
        if Lower == "arrowright":
            return "right"
        if Lower == "delete" or Lower == "del":
            return "delete"
        if Lower == "numpaddelete" or Lower == "numpaddecimal":
            return "delete"
        if Lower.startswith("f") and Lower[1:].isdigit():
            return Lower
        return None

    def _Handle_Key(self, Message, Client):
        if not self._Client_Has_Control(Client):
            return
        Action = Message.get("Action")
        Key_Value = Message.get("Key") or Message.get("Code")
        Key_Name = self._Normalize_Key_Name(Key_Value)
        if Key_Name is None:
            return
        Info = self._Clients_Info.get(Client)
        if Info:
            User_Name = Info.get("User") or ""
            User_Key = Info.get("User_Key") or ""
            self._Emit_Event({"Event": "Key", "User": User_Name, "User_Key": User_Key, "Action": Action, "Key": Key_Name, "Raw_Key": Key_Value})
        Special_Press_Keys = {"win", "alt", "shift"}
        try:
            if Key_Name in Special_Press_Keys:
                if Action == "down" or Action == "press":
                    pyautogui.press(Key_Name)
                return
            if Action == "down":
                pyautogui.keyDown(Key_Name)
            elif Action == "up":
                pyautogui.keyUp(Key_Name)
            elif Action == "press":
                pyautogui.press(Key_Name)
        except Exception:
            return

    def _Handle_Key_Combo(self, Message, Client):
        if not self._Client_Has_Control(Client):
            return
        Keys_Value = Message.get("Keys")
        if not isinstance(Keys_Value, (list, tuple)) or not Keys_Value:
            return
        Keys_Normalized = []
        for Key_Value in Keys_Value:
            Key_Name = self._Normalize_Key_Name(Key_Value)
            if Key_Name is not None:
                Keys_Normalized.append(Key_Name)
        if not Keys_Normalized:
            return
        Info = self._Clients_Info.get(Client)
        if Info:
            User_Name = Info.get("User") or ""
            User_Key = Info.get("User_Key") or ""
            self._Emit_Event({"Event": "Key_Combo", "User": User_Name, "User_Key": User_Key, "Keys": list(Keys_Normalized)})
        try:
            if Keys_Normalized == ["shift", "delete"] or Keys_Normalized == ["delete", "shift"]:
                pyautogui.keyDown("shift")
                pyautogui.press("delete")
                pyautogui.keyUp("shift")
            else:
                pyautogui.hotkey(*Keys_Normalized)
        except Exception:
            return

    def _Handle_Click(self, Message, Client):
        if not self._Client_Has_Control(Client):
            return
        X_Rel = Message.get("X")
        Y_Rel = Message.get("Y")
        if X_Rel is None or Y_Rel is None:
            return
        Button_Name = Message.get("Button") or "left"
        Action = Message.get("Action") or "click"
        try:
            X_Val = float(X_Rel)
            Y_Val = float(Y_Rel)
        except Exception:
            return
        X_Clamped = max(0.0, min(1.0, X_Val))
        Y_Clamped = max(0.0, min(1.0, Y_Val))
        Info = self._Clients_Info.get(Client)
        if Info and Action != "move":
            User_Name = Info.get("User") or ""
            User_Key = Info.get("User_Key") or ""
            self._Emit_Event({"Event": "Mouse", "User": User_Name, "User_Key": User_Key, "Button": Button_Name, "Action": Action, "X": X_Clamped, "Y": Y_Clamped})
        with self._Mouse_Lock:
            self._Mouse_Has_State = True
            self._Mouse_Button = Button_Name
            self._Mouse_X = X_Clamped
            self._Mouse_Y = Y_Clamped
            if Action == "down":
                self._Mouse_Down = True
            elif Action == "up":
                self._Mouse_Down = False

    def _Capture_Loop(self):
        with mss.mss() as Screen_Capture:
            while self._Running:
                Monitors = Screen_Capture.monitors
                if len(Monitors) > 1:
                    Actual_Count = len(Monitors) - 1
                else:
                    Actual_Count = 1
                with self._Monitor_Lock:
                    if self._Monitor_Count != Actual_Count:
                        self._Monitor_Count = Actual_Count
                    if self._Monitor_Count < 1:
                        self._Monitor_Count = 1
                    Monitor_Count_Local = self._Monitor_Count
                Clients_Snapshot = list(self._Clients_Info.items())
                for Monitor_Index in range(1, Monitor_Count_Local + 1):
                    Has_Viewer = False
                    for Client_Id, Client_Info in Clients_Snapshot:
                        if not Client_Info.get("Authenticated"):
                            continue
                        Monitor_Index_Client = Client_Info.get("Monitor_Index", 1)
                        if Monitor_Index_Client == Monitor_Index:
                            Has_Viewer = True
                            break
                    if not Has_Viewer:
                        continue
                    if len(Monitors) == 1:
                        Monitor = Monitors[0]
                    else:
                        if Monitor_Index >= len(Monitors):
                            continue
                        Monitor = Monitors[Monitor_Index]
                    try:
                        Sct_Image = Screen_Capture.grab(Monitor)
                    except Exception:
                        continue
                    try:
                        Frame_Bytes = mss.tools.to_png(Sct_Image.rgb, Sct_Image.size)
                    except Exception:
                        continue
                    for Client_Id, Client_Info in Clients_Snapshot:
                        if not Client_Info.get("Authenticated"):
                            continue
                        Monitor_Index_Client = Client_Info.get("Monitor_Index", 1)
                        if Monitor_Index_Client != Monitor_Index:
                            continue
                        try:
                            self._Socket.Send(Client_Id, Frame_Bytes)
                        except Exception:
                            pass
                time.sleep(self._Capture_Interval)

    def _Mouse_Loop(self):
        while self._Running:
            if self._Screen_Size is None:
                try:
                    self._Screen_Size = pyautogui.size()
                except Exception:
                    self._Screen_Size = None
            with self._Mouse_Lock:
                Has_State = self._Mouse_Has_State
                Down = self._Mouse_Down
                Button = self._Mouse_Button
                X_Val = self._Mouse_X
                Y_Val = self._Mouse_Y
                Prev_Down = self._Mouse_Prev_Down
                Prev_X = self._Mouse_Prev_X
                Prev_Y = self._Mouse_Prev_Y
            if self._Screen_Size is not None and Has_State:
                Screen_Width, Screen_Height = self._Screen_Size
                try:
                    X_Clamped = max(0.0, min(1.0, float(X_Val)))
                    Y_Clamped = max(0.0, min(1.0, float(Y_Val)))
                except Exception:
                    X_Clamped = 0.0
                    Y_Clamped = 0.0
                X_Abs = int(X_Clamped * Screen_Width)
                Y_Abs = int(Y_Clamped * Screen_Height)
                Move_Needed = True
                if Prev_X is not None and Prev_Y is not None:
                    Prev_X_Abs = int(Prev_X * Screen_Width)
                    Prev_Y_Abs = int(Prev_Y * Screen_Height)
                    if Prev_X_Abs == X_Abs and Prev_Y_Abs == Y_Abs:
                        Move_Needed = False
                try:
                    if Move_Needed:
                        pyautogui.moveTo(X_Abs, Y_Abs)
                    if Down and not Prev_Down:
                        pyautogui.mouseDown(x=X_Abs, y=Y_Abs, button=Button)
                    elif not Down and Prev_Down:
                        pyautogui.mouseUp(x=X_Abs, y=Y_Abs, button=Button)
                except Exception:
                    pass
                with self._Mouse_Lock:
                    self._Mouse_Prev_Down = Down
                    self._Mouse_Prev_X = X_Clamped
                    self._Mouse_Prev_Y = Y_Clamped
            time.sleep(0.002)

    def _Send_Monitor_Info(self, Client):
        with self._Monitor_Lock:
            Count = self._Monitor_Count
        Info = self._Clients_Info.get(Client)
        if Info and "Monitor_Index" in Info:
            Active = Info.get("Monitor_Index") or 1
        else:
            Active = 1
        if Active < 1:
            Active = 1
        if Count < 1:
            Count = 1
        if Active > Count:
            Active = Count
        Payload = json.dumps({"Type": "Monitors", "Count": Count, "Active": Active}, separators=(",", ":"))
        try:
            self._Socket.Send(Client, Payload)
        except Exception:
            pass

    def _Handle_Monitor_Select(self, Message, Client):
        Index_Value = Message.get("Index")
        try:
            Index_Int = int(Index_Value)
        except Exception:
            return
        if Index_Int < 1:
            Index_Int = 1
        with self._Monitor_Lock:
            Count = self._Monitor_Count
            if Count < 1:
                Count = 1
                self._Monitor_Count = 1
            if Index_Int > Count:
                Index_Int = Count
            Count_Out = self._Monitor_Count
        Info = self._Clients_Info.get(Client) or {}
        Info["Monitor_Index"] = Index_Int
        self._Clients_Info[Client] = Info
        Active = Index_Int
        Payload = json.dumps({"Type": "Monitors", "Count": Count_Out, "Active": Active}, separators=(",", ":"))
        try:
            self._Socket.Send(Client, Payload)
        except Exception:
            pass

    def _Client_Has_Control(self, Client):
        Info = self._Clients_Info.get(Client)
        if not Info:
            return False
        if not Info.get("Authenticated"):
            return False
        if not Info.get("Control_Allowed"):
            return False
        if not Info.get("Has_Control"):
            return False
        User_Key = Info.get("User_Key")
        if not User_Key:
            return False
        if User_Key != self._Controller_User_Key:
            return False
        return True

    def _Send_Login_Result(self, Client, Success, Error_Text, Controller_Name, Control_Allowed):
        Payload = {"Type": "Login_Result", "Success": bool(Success)}
        if Error_Text:
            Payload["Error"] = str(Error_Text)
        if Controller_Name:
            Payload["Controller"] = str(Controller_Name)
        if Success:
            Payload["Control"] = bool(Control_Allowed)
            Info = self._Clients_Info.get(Client)
            if Info:
                Payload["User"] = Info.get("User") or ""
                Payload["Active"] = bool(Info.get("Has_Control"))
            else:
                Payload["User"] = ""
                Payload["Active"] = False
        Payload_Text = json.dumps(Payload, separators=(",", ":"))
        try:
            self._Socket.Send(Client, Payload_Text)
        except Exception:
            pass

    def _Handle_Control_Release(self, Message, Client):
        Info = self._Clients_Info.get(Client)
        if not Info or not Info.get("Authenticated") or not Info.get("Control_Allowed"):
            return
        User_Key = Info.get("User_Key")
        if not User_Key:
            return
        if self._Controller_User_Key != User_Key:
            return
        for Client_Id, Client_Info in list(self._Clients_Info.items()):
            if Client_Info.get("User_Key") == User_Key and Client_Info.get("Authenticated") and Client_Info.get("Control_Allowed"):
                Client_Info["Has_Control"] = False
                self._Clients_Info[Client_Id] = Client_Info
                Payload = {
                    "Type": "Control_Changed",
                    "Active": False,
                    "Controller": ""
                }
                Payload_Text = json.dumps(Payload, separators=(",", ":"))
                try:
                    self._Socket.Send(Client_Id, Payload_Text)
                except Exception:
                    pass
        self._Controller_User_Key = None
        self._Controller_User = None
        User_Name = Info.get("User") or ""
        self._Emit_Event({"Event": "Control_Change", "Type": "Release", "User": User_Name, "User_Key": User_Key, "Forced": False})

    def _Handle_Control_Request(self, Message, Client):
        Info = self._Clients_Info.get(Client)
        if not Info or not Info.get("Authenticated"):
            return
        Control_Allowed = bool(Info.get("Control_Allowed"))
        if not Control_Allowed:
            Payload = {
                "Type": "Control_Result",
                "Success": False,
                "Active": False,
                "Error": "Not allowed"
            }
            Payload_Text = json.dumps(Payload, separators=(",", ":"))
            try:
                self._Socket.Send(Client, Payload_Text)
            except Exception:
                pass
            return
        User_Key = Info.get("User_Key")
        User_Name = Info.get("User") or ""
        if not User_Key:
            return
        if self._Controller_User_Key is not None:
            Controller_Alive = False
            for Other_Info in self._Clients_Info.values():
                if Other_Info.get("User_Key") == self._Controller_User_Key and Other_Info.get("Authenticated") and Other_Info.get("Control_Allowed"):
                    Controller_Alive = True
                    break
            if not Controller_Alive:
                self._Controller_User_Key = None
                self._Controller_User = None
        Force_Flag = bool(Message.get("Force"))
        if self._Controller_User_Key is None:
            self._Controller_User_Key = User_Key
            self._Controller_User = User_Name
            for Client_Id, Client_Info in list(self._Clients_Info.items()):
                if Client_Info.get("User_Key") == User_Key and Client_Info.get("Authenticated") and Client_Info.get("Control_Allowed"):
                    Client_Info["Has_Control"] = True
                    self._Clients_Info[Client_Id] = Client_Info
                    Change_Payload = {
                        "Type": "Control_Changed",
                        "Active": True,
                        "Controller": self._Controller_User or ""
                    }
                    Change_Text = json.dumps(Change_Payload, separators=(",", ":"))
                    try:
                        self._Socket.Send(Client_Id, Change_Text)
                    except Exception:
                        pass
            Result_Payload = {
                "Type": "Control_Result",
                "Success": True,
                "Active": True,
                "Controller": self._Controller_User or ""
            }
            Result_Text = json.dumps(Result_Payload, separators=(",", ":"))
            try:
                self._Socket.Send(Client, Result_Text)
            except Exception:
                pass
            self._Emit_Event({"Event": "Control_Change", "Type": "Acquire", "User": User_Name, "User_Key": User_Key, "Forced": Force_Flag, "Previous_Controller_User": "", "Previous_Controller_Key": ""})
            return
        if self._Controller_User_Key == User_Key:
            for Client_Id, Client_Info in list(self._Clients_Info.items()):
                if Client_Info.get("User_Key") == User_Key and Client_Info.get("Authenticated") and Client_Info.get("Control_Allowed"):
                    Client_Info["Has_Control"] = True
                    self._Clients_Info[Client_Id] = Client_Info
            Result_Payload = {
                "Type": "Control_Result",
                "Success": True,
                "Active": True,
                "Controller": self._Controller_User or ""
            }
            Result_Text = json.dumps(Result_Payload, separators=(",", ":"))
            try:
                self._Socket.Send(Client, Result_Text)
            except Exception:
                pass
            self._Emit_Event({"Event": "Control_Change", "Type": "Acquire", "User": User_Name, "User_Key": User_Key, "Forced": Force_Flag, "Previous_Controller_User": self._Controller_User or "", "Previous_Controller_Key": self._Controller_User_Key or ""})
            return
        Current_Controller_Name = self._Controller_User or ""
        if not Force_Flag:
            Result_Payload = {
                "Type": "Control_Result",
                "Success": False,
                "Active": False,
                "In_Use": True,
                "Controller": Current_Controller_Name
            }
            Result_Text = json.dumps(Result_Payload, separators=(",", ":"))
            try:
                self._Socket.Send(Client, Result_Text)
            except Exception:
                pass
            return
        Old_Key = self._Controller_User_Key
        Old_Name = self._Controller_User or ""
        for Client_Id, Client_Info in list(self._Clients_Info.items()):
            if Client_Info.get("User_Key") == Old_Key and Client_Info.get("Authenticated") and Client_Info.get("Control_Allowed"):
                Client_Info["Has_Control"] = False
                self._Clients_Info[Client_Id] = Client_Info
                Lose_Payload = {
                    "Type": "Control_Changed",
                    "Active": False,
                    "Controller": User_Name
                }
                Lose_Text = json.dumps(Lose_Payload, separators=(",", ":"))
                try:
                    self._Socket.Send(Client_Id, Lose_Text)
                except Exception:
                    pass
        self._Controller_User_Key = User_Key
        self._Controller_User = User_Name
        for Client_Id, Client_Info in list(self._Clients_Info.items()):
            if Client_Info.get("User_Key") == User_Key and Client_Info.get("Authenticated") and Client_Info.get("Control_Allowed"):
                Client_Info["Has_Control"] = True
                self._Clients_Info[Client_Id] = Client_Info
                Gain_Payload = {
                    "Type": "Control_Changed",
                    "Active": True,
                    "Controller": self._Controller_User or ""
                }
                Gain_Text = json.dumps(Gain_Payload, separators=(",", ":"))
                try:
                    self._Socket.Send(Client_Id, Gain_Text)
                except Exception:
                    pass
        Result_Payload = {
            "Type": "Control_Result",
            "Success": True,
            "Active": True,
            "Controller": self._Controller_User or ""
        }
        Result_Text = json.dumps(Result_Payload, separators=(",", ":"))
        try:
            self._Socket.Send(Client, Result_Text)
        except Exception:
            pass
        self._Emit_Event({"Event": "Control_Change", "Type": "Force", "User": User_Name, "User_Key": User_Key, "Forced": True, "Previous_Controller_User": Old_Name, "Previous_Controller_Key": Old_Key})

    def _Handle_Login(self, Message, Client):
        User_Name = Message.get("User")
        Password_Text = Message.get("Password")
        if not isinstance(User_Name, str) or not isinstance(Password_Text, str):
            self._Send_Login_Result(Client, False, "Invalid login", None, False)
            return
        User_Name_Text = User_Name.strip()
        if not User_Name_Text:
            self._Send_Login_Result(Client, False, "Invalid username or password", None, False)
            return
        User_Key = User_Name_Text.lower()
        User_Info = self._Users.get(User_Key)
        if not User_Info:
            self._Send_Login_Result(Client, False, "Invalid username or password", None, False)
            return
        Stored_Password = str(User_Info.get("Password") or "")
        Expected_Hash = hashlib.md5(Stored_Password.encode("utf-8")).hexdigest()
        if Expected_Hash != Password_Text:
            self._Send_Login_Result(Client, False, "Invalid username or password", None, False)
            return
        Control_Allowed = bool(User_Info.get("Control"))
        Info = self._Clients_Info.get(Client) or {}
        Info["User"] = User_Name_Text
        Info["User_Key"] = User_Key
        Info["Authenticated"] = True
        Info["Control_Allowed"] = Control_Allowed
        Info["Has_Control"] = False
        if "Monitor_Index" not in Info:
            Info["Monitor_Index"] = 1
        self._Clients_Info[Client] = Info
        Controller_Name = self._Controller_User
        self._Send_Login_Result(Client, True, None, Controller_Name, Control_Allowed)
        self._Send_Monitor_Info(Client)
        self._Emit_Event({"Event": "Login", "User": User_Name_Text, "User_Key": User_Key, "Control_Allowed": Control_Allowed})

    def _Handle_Logout(self, Client):
        Info = self._Clients_Info.get(Client)
        if not Info:
            return
        User_Key = Info.get("User_Key")
        User_Name = Info.get("User") or ""
        if Info.get("Has_Control"):
            if User_Key and User_Key == self._Controller_User_Key:
                self._Controller_User_Key = None
                self._Controller_User = None
                self._Emit_Event({"Event": "Control_Change", "Type": "Logout", "User": User_Name, "User_Key": User_Key})
        Info["Authenticated"] = False
        Info["Control_Allowed"] = False
        Info["Has_Control"] = False
        self._Clients_Info[Client] = Info
        if User_Key:
            self._Emit_Event({"Event": "Logout", "User": User_Name, "User_Key": User_Key})

    def _Run_Forever(self):
        try:
            while self._Running:
                time.sleep(1.0)
        except KeyboardInterrupt:
            self.Stop()

    def _Prepare_Web_Root(self):
        os.makedirs(self._Web_Root, exist_ok=True)
        Index_Path = os.path.join(self._Web_Root, "index.html")
        Html = self._Build_Index_Html()
        with open(Index_Path, "w", encoding="utf-8") as F:
            F.write(Html)

    def _Build_Index_Html(self):
        Html = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Nucleon VNC</title>
<style>
html, body {
    margin: 0;
    padding: 0;
    width: 100%;
    height: 100%;
    background-color: #000000;
    overflow: hidden;
}
#Login_Overlay {
    position: fixed;
    left: 0;
    top: 0;
    right: 0;
    bottom: 0;
    background-color: #000000;
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 30;
}
#Login_Box {
    min-width: 260px;
    max-width: 320px;
    padding: 14px 16px;
    background-color: #202020;
    border-radius: 8px;
    border: 1px solid #404040;
    color: #ffffff;
    font-family: sans-serif;
    font-size: 12px;
    box-sizing: border-box;
    box-shadow: 0 0 14px rgba(0,0,0,0.7);
}
#Login_Title {
    font-size: 14px;
    font-weight: bold;
    margin-bottom: 8px;
}
#Login_Error {
    min-height: 16px;
    font-size: 11px;
    color: #ec7063;
    margin-bottom: 6px;
}
.Login_Label {
    font-size: 11px;
    margin-top: 4px;
    margin-bottom: 2px;
}
.Login_Input {
    width: 100%;
    box-sizing: border-box;
    padding: 4px 6px;
    border-radius: 4px;
    border: 1px solid #606060;
    background-color: #101010;
    color: #ffffff;
    font-size: 12px;
}
#Login_Button {
    margin-top: 8px;
    width: 100%;
    padding: 5px 0;
    border-radius: 4px;
    border: 1px solid #0078d7;
    background-color: #0078d7;
    color: #ffffff;
    font-size: 12px;
    cursor: pointer;
}
#Side_Bar {
    position: fixed;
    left: 0;
    top: 40px;
    background-color: #202020;
    color: #ffffff;
    font-family: sans-serif;
    font-size: 11px;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    border-radius: 8px;
    box-shadow: 0 0 10px rgba(0, 0, 0, 0.6);
    border: 1px solid #404040;
    z-index: 10;
    overflow: hidden;
    width: 24px;
    transition: width 0.2s ease-out;
}
#Side_Bar.Side_Bar_Expanded {
    width: 120px;
}
#Side_Bar_Header {
    height: 26px;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    padding: 3px 4px;
    box-sizing: border-box;
    background-color: #252525;
    border-bottom: 1px solid #404040;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    cursor: move;
}
#Side_Toggle_Circle {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: none;
    cursor: pointer;
    flex-shrink: 0;
    box-shadow: none;
    outline: none;
}
#Side_Toggle_Circle.Side_Toggle_Circle_Signal_Green {
    background-color: #58d68d;
}
#Side_Toggle_Circle.Side_Toggle_Circle_Signal_Orange {
    background-color: #eb984e;
}
#Side_Toggle_Circle.Side_Toggle_Circle_Signal_Red {
    background-color: #ec7063;
}
#Side_Title {
    font-size: 11px;
    font-weight: bold;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-left: 6px;
    display: none;
}
#Side_Bar.Side_Bar_Expanded #Side_Title {
    display: inline-block;
}
#Side_Content {
    display: none;
    flex-direction: column;
    padding: 5px;
    box-sizing: border-box;
    gap: 6px;
}
#Side_Bar.Side_Bar_Expanded #Side_Content {
    display: flex;
}
.Side_Section_Title {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #a0a0a0;
    margin-bottom: 3px;
}
#User_Section {
    display: flex;
    flex-direction: column;
    gap: 2px;
}
#User_Label {
    font-size: 9px;
    color: #c0c0c0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
#Control_Button {
    padding: 1px 3px;
    border-radius: 4px;
    border: 1px solid #ffffff;
    font-size: 10px;
    cursor: pointer;
    background-color: #202020;
    color: #ffffff;
    text-align: center;
    width: 100%;
    box-sizing: border-box;
}
#Control_Message {
    font-size: 10px;
    color: #f0f0f0;
    margin-top: 2px;
    min-height: 14px;
    display: none;
}
#Control_Confirm_Bar {
    display: none;
    margin-top: 3px;
    gap: 4px;
}
.Control_Confirm_Button {
    padding: 1px 3px;
    border-radius: 4px;
    border: 1px solid #ffffff;
    font-size: 10px;
    cursor: pointer;
    background-color: #202020;
    color: #ffffff;
    flex: 1;
    text-align: center;
}
#Logout_Button {
    margin-top: 2px;
    padding: 1px 3px;
    border-radius: 4px;
    border: 1px solid #606060;
    font-size: 10px;
    cursor: pointer;
    background-color: #303030;
    color: #ffffff;
    text-align: center;
    width: 100%;
    box-sizing: border-box;
}
#Scale_Bar {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 2px;
}
.Scale_Button {
    padding: 1px 3px;
    border-radius: 4px;
    border: 1px solid #ffffff;
    font-size: 10px;
    cursor: pointer;
    background-color: #202020;
    color: #ffffff;
    text-align: center;
}
.Scale_Button_Active {
    background-color: #0078d7;
    border-color: #0078d7;
}
#Monitor_Bar {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
}
.Monitor_Button {
    min-width: 24px;
    padding: 1px 5px;
    border: 1px solid #ffffff;
    border-radius: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    cursor: pointer;
    background-color: #ffffff;
    color: #000000;
}
.Monitor_Button_Active {
    background-color: #0078d7;
    color: #ffffff;
    border-color: #0078d7;
}
#Fullscreen_Button {
    padding: 1px 3px;
    border-radius: 4px;
    border: 1px solid #ffffff;
    font-size: 10px;
    cursor: pointer;
    background-color: #202020;
    color: #ffffff;
    text-align: center;
    width: 100%;
    box-sizing: border-box;
}
.Key_Combos_Button {
    padding: 1px 3px;
    border-radius: 4px;
    border: 1px solid #ffffff;
    font-size: 10px;
    cursor: pointer;
    background-color: #202020;
    color: #ffffff;
    text-align: center;
    width: 100%;
    box-sizing: border-box;
}
#Basic_Combos_Bar {
    display: flex;
    flex-wrap: wrap;
    gap: 3px;
    margin-bottom: 4px;
}
.Combo_Icon_Button {
    width: 20px;
    height: 20px;
    padding: 0;
    margin: 0;
    border-radius: 4px;
    border: 1px solid #ffffff;
    background-color: #303030;
    cursor: pointer;
    box-sizing: border-box;
    background-repeat: no-repeat;
    background-position: center;
    background-size: 12px 12px;
}
.Combo_Icon_Button:active {
    background-color: #505050;
}
.Combo_Icon_Copy {
    background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect x="4" y="3" width="8" height="9" fill="%23ffffff"/><rect x="2" y="5" width="8" height="9" fill="none" stroke="%23ffffff" stroke-width="1"/></svg>');
}
.Combo_Icon_Paste {
    background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect x="4" y="3" width="8" height="10" fill="none" stroke="%23ffffff" stroke-width="1"/><rect x="6" y="1" width="4" height="3" fill="%23ffffff"/></svg>');
}
.Combo_Icon_Cut {
    background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle cx="4" cy="5" r="2" fill="none" stroke="%23ffffff" stroke-width="1"/><circle cx="4" cy="11" r="2" fill="none" stroke="%23ffffff" stroke-width="1"/><line x1="7" y1="4" x2="12" y2="1" stroke="%23ffffff" stroke-width="1"/><line x1="7" y1="12" x2="12" y2="15" stroke="%23ffffff" stroke-width="1"/><line x1="7" y1="8" x2="11" y2="8" stroke="%23ffffff" stroke-width="1"/></svg>');
}
.Combo_Icon_Undo {
    background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M6 4 L3 7 L6 10" fill="none" stroke="%23ffffff" stroke-width="1"/><path d="M3 7 H9 A4 4 0 0 1 9 15" fill="none" stroke="%23ffffff" stroke-width="1"/></svg>');
}
#Screen_Container {
    position: fixed;
    left: 0;
    top: 0;
    right: 0;
    bottom: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background-color: #000000;
}
#Screen_Image {
    max-width: 100%;
    max-height: 100%;
    image-rendering: pixelated;
    object-fit: contain;
}
#Key_Combo_Popup {
    position: fixed;
    left: 140px;
    top: 80px;
    min-width: 180px;
    max-width: 260px;
    background-color: #202020;
    color: #ffffff;
    font-family: sans-serif;
    font-size: 11px;
    border-radius: 8px;
    box-shadow: 0 0 10px rgba(0, 0, 0, 0.7);
    border: 1px solid #404040;
    z-index: 20;
    display: none;
    box-sizing: border-box;
}
#Key_Combo_Header {
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 6px;
    background-color: #252525;
    border-bottom: 1px solid #404040;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    cursor: move;
    box-sizing: border-box;
}
#Key_Combo_Title {
    font-size: 11px;
    font-weight: bold;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
#Key_Combo_Close {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #606060;
    background-color: #303030;
    color: #ffffff;
    font-size: 10px;
    cursor: pointer;
    padding: 0;
    text-align: center;
}
#Key_Combo_Content {
    padding: 6px;
    box-sizing: border-box;
}
#Key_Combo_List {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
}
.Key_Combo_Button {
    padding: 2px 5px;
    border-radius: 4px;
    border: 1px solid #ffffff;
    font-size: 10px;
    cursor: pointer;
    background-color: #303030;
    color: #ffffff;
    white-space: nowrap;
}
#Control_Button,
.Control_Confirm_Button,
#Logout_Button,
#Fullscreen_Button,
.Key_Combos_Button,
.Scale_Button,
.Monitor_Button,
.Key_Combo_Button,
.Combo_Icon_Button {
    user-select: none;
    -webkit-user-select: none;
    -moz-user-select: none;
    -ms-user-select: none;
}
</style>
</head>
<body>
<div id="Login_Overlay">
    <div id="Login_Box">
        <div id="Login_Title">Nucleon VNC</div>
        <div id="Login_Error"></div>
        <div class="Login_Label">Username</div>
        <input id="Login_User" class="Login_Input" type="text" autocomplete="username" />
        <div class="Login_Label">Password</div>
        <input id="Login_Password" class="Login_Input" type="password" autocomplete="current-password" />
        <button id="Login_Button">Login</button>
    </div>
</div>
<div id="Side_Bar">
    <div id="Side_Bar_Header">
        <div id="Side_Toggle_Circle" class="Side_Toggle_Circle_Signal_Orange"></div>
        <span id="Side_Title">Nucleon VNC</span>
    </div>
    <div id="Side_Content">
        <div id="User_Section">
            <div class="Side_Section_Title">User</div>
            <div id="User_Label"></div>
            <button id="Control_Button">Take Control</button>
            <div id="Control_Message"></div>
            <div id="Control_Confirm_Bar">
                <button id="Control_Yes_Button" class="Control_Confirm_Button">Yes</button>
                <button id="Control_No_Button" class="Control_Confirm_Button">No</button>
            </div>
            <button id="Logout_Button">Logout</button>
        </div>
        <div id="View_Section">
            <div class="Side_Section_Title">Fullscreen</div>
            <button id="Fullscreen_Button">Enter</button>
        </div>
        <div id="Keys_Section">
            <div class="Side_Section_Title">Keys</div>
            <div id="Basic_Combos_Bar">
                <button class="Combo_Icon_Button Combo_Icon_Copy" data-keys="ctrl+c" title="Copy"></button>
                <button class="Combo_Icon_Button Combo_Icon_Paste" data-keys="ctrl+v" title="Paste"></button>
                <button class="Combo_Icon_Button Combo_Icon_Cut" data-keys="ctrl+x" title="Cut"></button>
                <button class="Combo_Icon_Button Combo_Icon_Undo" data-keys="ctrl+z" title="Undo"></button>
            </div>
            <button id="Key_Combos_Button" class="Key_Combos_Button">Key Combos</button>
        </div>
        <div id="Scale_Section">
            <div class="Side_Section_Title">Scale</div>
            <div id="Scale_Bar">
                <div class="Scale_Button Scale_Button_Active" data-mode="fit">Fit</div>
                <div class="Scale_Button" data-mode="actual">1:1</div>
                <div class="Scale_Button" data-mode="stretch">Stretched</div>
            </div>
        </div>
        <div id="Monitor_Section">
            <div class="Side_Section_Title">Monitors</div>
            <div id="Monitor_Bar"></div>
        </div>
    </div>
</div>
<div id="Key_Combo_Popup">
    <div id="Key_Combo_Header">
        <span id="Key_Combo_Title">Key Combinations</span>
        <button id="Key_Combo_Close">×</button>
    </div>
    <div id="Key_Combo_Content">
        <div id="Key_Combo_List">
            <div class="Key_Combo_Button" data-keys="ctrl+alt+delete">Ctrl+Alt+Del</div>
            <div class="Key_Combo_Button" data-keys="ctrl+shift+esc">Ctrl+Shift+Esc</div>
            <div class="Key_Combo_Button" data-keys="ctrl+shift+delete">Ctrl+Shift+Del</div>
            <div class="Key_Combo_Button" data-keys="alt+tab">Alt+Tab</div>
            <div class="Key_Combo_Button" data-keys="alt+f4">Alt+F4</div>
            <div class="Key_Combo_Button" data-keys="win">Win</div>
            <div class="Key_Combo_Button" data-keys="win+r">Win+R</div>
            <div class="Key_Combo_Button" data-keys="win+l">Win+L</div>
            <div class="Key_Combo_Button" data-keys="win+d">Win+D</div>
            <div class="Key_Combo_Button" data-keys="win+e">Win+E</div>
            <div class="Key_Combo_Button" data-keys="win+ctrl+shift+b">Win+Ctrl+Shift+B</div>
            <div class="Key_Combo_Button" data-keys="shift+delete">Shift+Delete</div>
        </div>
    </div>
</div>
<div id="Screen_Container">
    <img id="Screen_Image" src="" draggable="false" />
</div>
<script>
var Web_Socket = null;
var Screen_Image = null;
var Mouse_Is_Down = false;
var Mouse_Button = "left";
var Monitor_Bar = null;
var Reconnect_Timer = null;
var Monitor_Count = 1;
var Monitor_Active = 1;
var Frame_Object_Url = null;
var Side_Bar = null;
var Side_Bar_Header = null;
var Side_Toggle_Circle = null;
var Scale_Bar = null;
var Scale_Mode = "fit";
var Keyboard_Active = false;
var Side_Bar_Dragging = false;
var Side_Bar_Drag_Offset_X = 0;
var Side_Bar_Drag_Offset_Y = 0;
var Side_Bar_Did_Drag = false;
var Side_Bar_Last_Mouse_X = 0;
var Side_Bar_Last_Mouse_Y = 0;
var Fullscreen_Button = null;
var Fullscreen_Active = false;
var Side_Bar_Auto_Close_Timer = null;
var Side_Bar_Is_Hover = false;
var Key_Combo_Popup = null;
var Key_Combo_Header = null;
var Key_Combo_Close = null;
var Key_Combos_Button = null;
var Key_Combo_Dragging = false;
var Key_Combo_Drag_Offset_X = 0;
var Key_Combo_Drag_Offset_Y = 0;
var Key_Combo_Last_Mouse_X = 0;
var Key_Combo_Last_Mouse_Y = 0;
var Login_Overlay = null;
var Login_User = null;
var Login_Password = null;
var Login_Button = null;
var Login_Error = null;
var Login_Authenticated = false;
var Control_Allowed = false;
var Control_Active = false;
var Current_User_Name = "";
var User_Label = null;
var Control_Button = null;
var Logout_Button = null;
var Control_Message = null;
var Control_Confirm_Bar = null;
var Control_Yes_Button = null;
var Control_No_Button = null;
var Force_Control_Pending = false;

var Md5_T_Values = [];
var Md5_Shift_Amounts = [
    7,12,17,22, 7,12,17,22, 7,12,17,22, 7,12,17,22,
    5,9,14,20, 5,9,14,20, 5,9,14,20, 5,9,14,20,
    4,11,16,23, 4,11,16,23, 4,11,16,23, 4,11,16,23,
    6,10,15,21, 6,10,15,21, 6,10,15,21, 6,10,15,21
];

function Md5_Initialize_Constants() {
    if (Md5_T_Values.length === 64) {
        return;
    }
    for (var Index_I = 0; Index_I < 64; Index_I++) {
        Md5_T_Values[Index_I] = (Math.abs(Math.sin(Index_I + 1)) * 4294967296) | 0;
    }
}

function Md5_Rotate_Left(Value, Shift_Amount) {
    return (Value << Shift_Amount) | (Value >>> (32 - Shift_Amount));
}

function Md5_Word_To_Hex(Value) {
    var Hex_Text = "";
    for (var Index_Byte = 0; Index_Byte < 4; Index_Byte++) {
        var Byte_Value = (Value >>> (Index_Byte * 8)) & 255;
        var Hex_Part = Byte_Value.toString(16);
        if (Hex_Part.length < 2) {
            Hex_Part = "0" + Hex_Part;
        }
        Hex_Text += Hex_Part;
    }
    return Hex_Text;
}

function Md5_Hash_Text(Text) {
    Md5_Initialize_Constants();
    if (typeof Text !== "string") {
        Text = String(Text);
    }
    var Utf8_Text = unescape(encodeURIComponent(Text));
    var Msg_Length = Utf8_Text.length;
    var Num_Words_Temp1 = Msg_Length + 8;
    var Num_Words_Temp2 = (Num_Words_Temp1 >>> 6) + 1;
    var Total_Words = Num_Words_Temp2 * 16;
    var Words = new Array(Total_Words);
    for (var Index_Init = 0; Index_Init < Total_Words; Index_Init++) {
        Words[Index_Init] = 0;
    }
    for (var Index_Byte = 0; Index_Byte < Msg_Length; Index_Byte++) {
        var Word_Index = (Index_Byte >>> 2);
        var Byte_Position = (Index_Byte % 4) * 8;
        Words[Word_Index] = Words[Word_Index] | (Utf8_Text.charCodeAt(Index_Byte) << Byte_Position);
    }
    var Pad_Word_Index = (Msg_Length >>> 2);
    var Pad_Byte_Position = (Msg_Length % 4) * 8;
    Words[Pad_Word_Index] = Words[Pad_Word_Index] | (0x80 << Pad_Byte_Position);
    var Bit_Length = Msg_Length * 8;
    Words[Total_Words - 2] = Bit_Length & 0xffffffff;
    Words[Total_Words - 1] = (Bit_Length / 0x100000000) | 0;
    var A0 = 0x67452301;
    var B0 = 0xefcdab89;
    var C0 = 0x98badcfe;
    var D0 = 0x10325476;
    for (var Index_Block = 0; Index_Block < Total_Words; Index_Block += 16) {
        var A = A0;
        var B = B0;
        var C = C0;
        var D = D0;
        for (var Index_Step = 0; Index_Step < 64; Index_Step++) {
            var F_Value;
            var Index_G;
            if (Index_Step < 16) {
                F_Value = (B & C) | ((~B) & D);
                Index_G = Index_Step;
            } else if (Index_Step < 32) {
                F_Value = (D & B) | ((~D) & C);
                Index_G = (5 * Index_Step + 1) % 16;
            } else if (Index_Step < 48) {
                F_Value = B ^ C ^ D;
                Index_G = (3 * Index_Step + 5) % 16;
            } else {
                F_Value = C ^ (B | (~D));
                Index_G = (7 * Index_Step) % 16;
            }
            var Temp_D = D;
            D = C;
            C = B;
            var Temp_Sum = (A + F_Value + Md5_T_Values[Index_Step] + Words[Index_Block + Index_G]) | 0;
            B = (B + Md5_Rotate_Left(Temp_Sum, Md5_Shift_Amounts[Index_Step])) | 0;
            A = Temp_D;
        }
        A0 = (A0 + A) | 0;
        B0 = (B0 + B) | 0;
        C0 = (C0 + C) | 0;
        D0 = (D0 + D) | 0;
    }
    var Result = Md5_Word_To_Hex(A0) + Md5_Word_To_Hex(B0) + Md5_Word_To_Hex(C0) + Md5_Word_To_Hex(D0);
    return Result.toLowerCase();
}

function Set_Status_State(State) {
    if (!Side_Toggle_Circle) {
        return;
    }
    Side_Toggle_Circle.classList.remove("Side_Toggle_Circle_Signal_Green");
    Side_Toggle_Circle.classList.remove("Side_Toggle_Circle_Signal_Orange");
    Side_Toggle_Circle.classList.remove("Side_Toggle_Circle_Signal_Red");
    if (State === "green") {
        Side_Toggle_Circle.classList.add("Side_Toggle_Circle_Signal_Green");
    } else if (State === "orange") {
        Side_Toggle_Circle.classList.add("Side_Toggle_Circle_Signal_Orange");
    } else {
        Side_Toggle_Circle.classList.add("Side_Toggle_Circle_Signal_Red");
    }
}

function Start_Connection() {
    if (Web_Socket && (Web_Socket.readyState === WebSocket.OPEN || Web_Socket.readyState === WebSocket.CONNECTING)) {
        try {
            Web_Socket.close();
        } catch (E) {}
    }
    var Protocol = window.location.protocol === "https:" ? "wss://" : "ws://";
    var Host = window.location.hostname;
    var Port = "VNC_Port_HERE";
    var Url = Protocol + Host + ":" + Port + "/";
    Set_Status_State("orange");
    Mouse_Is_Down = false;
    Web_Socket = new WebSocket(Url);
    Web_Socket.binaryType = "arraybuffer";
    Web_Socket.onopen = function(Event) {
        if (Reconnect_Timer !== null) {
            clearTimeout(Reconnect_Timer);
            Reconnect_Timer = null;
        }
        Set_Status_State("green");
    };
    Web_Socket.onmessage = function(Event) {
        var Data = Event.data;
        if (typeof Data !== "string") {
            Handle_Frame(Data);
            return;
        }
        var Msg = null;
        try {
            Msg = JSON.parse(Data);
        } catch (E) {
            return;
        }
        if (!Msg || !Msg.Type) {
            return;
        }
        if (Msg.Type === "Login_Result") {
            Handle_Login_Result(Msg);
        } else if (Msg.Type === "Monitors") {
            Handle_Monitors(Msg);
        } else if (Msg.Type === "Control_Result") {
            Handle_Control_Result(Msg);
        } else if (Msg.Type === "Control_Changed") {
            Handle_Control_Changed(Msg);
        }
    };
    Web_Socket.onclose = function(Event) {
        Handle_Disconnect();
    };
    Web_Socket.onerror = function(Event) {
        Handle_Disconnect();
    };
}

function Handle_Frame(Data) {
    if (!Screen_Image) {
        return;
    }
    if (!(Data instanceof ArrayBuffer)) {
        return;
    }
    if (Frame_Object_Url) {
        try {
            URL.revokeObjectURL(Frame_Object_Url);
        } catch (E) {}
        Frame_Object_Url = null;
    }
    var Blob_Obj = new Blob([Data], { type: "image/png" });
    Frame_Object_Url = URL.createObjectURL(Blob_Obj);
    Screen_Image.src = Frame_Object_Url;
}

function Handle_Disconnect() {
    Mouse_Is_Down = false;
    Login_Authenticated = false;
    Control_Allowed = false;
    Control_Active = false;
    Clear_Control_Message();
    Hide_Force_Control_Prompt();
    Update_Control_UI();
    if (Login_Overlay) {
        Login_Overlay.style.display = "flex";
    }
    Set_Status_State("red");
    if (Reconnect_Timer === null) {
        Reconnect_Timer = setTimeout(function() {
            Reconnect_Timer = null;
            Start_Connection();
        }, 2000);
    }
}

function Handle_Monitors(Msg) {
    var Count = Msg.Count;
    var Active = Msg.Active;
    if (typeof Count !== "number") {
        Count = 1;
    }
    if (typeof Active !== "number") {
        Active = 1;
    }
    Monitor_Count = Count;
    Monitor_Active = Active;
    Refresh_Monitors();
}

function Refresh_Monitors() {
    if (!Monitor_Bar) {
        return;
    }
    Monitor_Bar.innerHTML = "";
    var Count = Monitor_Count || 1;
    for (var I = 1; I <= Count; I++) {
        var Btn = document.createElement("div");
        Btn.className = "Monitor_Button" + (I === Monitor_Active ? " Monitor_Button_Active" : "");
        Btn.textContent = I.toString();
        (function(Index_Value) {
            Btn.addEventListener("click", function(E) {
                if (!Web_Socket || Web_Socket.readyState !== WebSocket.OPEN) {
                    return;
                }
                var Msg = { Type: "Monitor_Select", Index: Index_Value };
                Web_Socket.send(JSON.stringify(Msg));
                Schedule_Side_Bar_Auto_Close();
                E.preventDefault();
            });
        })(I);
        Monitor_Bar.appendChild(Btn);
    }
}

function Schedule_Side_Bar_Auto_Close() {
    if (Side_Bar_Auto_Close_Timer !== null) {
        clearTimeout(Side_Bar_Auto_Close_Timer);
    }
    Side_Bar_Auto_Close_Timer = setTimeout(function() {
        if (!Side_Bar) {
            return;
        }
        if (Side_Bar_Is_Hover) {
            Schedule_Side_Bar_Auto_Close();
            return;
        }
        Side_Bar.classList.remove("Side_Bar_Expanded");
    }, 7000);
}

function Open_Side_Bar_On_Hover(E) {
    if (!Side_Bar) {
        return;
    }
    if (!Side_Bar.classList.contains("Side_Bar_Expanded")) {
        Side_Bar.classList.add("Side_Bar_Expanded");
        Schedule_Side_Bar_Auto_Close();
    }
}

function Apply_Scale_Mode() {
    if (!Screen_Image) {
        return;
    }
    if (Scale_Mode === "actual") {
        Screen_Image.style.maxWidth = "none";
        Screen_Image.style.maxHeight = "none";
        Screen_Image.style.width = "auto";
        Screen_Image.style.height = "auto";
        Screen_Image.style.objectFit = "none";
    } else if (Scale_Mode === "stretch") {
        Screen_Image.style.maxWidth = "100%";
        Screen_Image.style.maxHeight = "100%";
        Screen_Image.style.width = "100%";
        Screen_Image.style.height = "100%";
        Screen_Image.style.objectFit = "fill";
    } else {
        Screen_Image.style.maxWidth = "100%";
        Screen_Image.style.maxHeight = "100%";
        Screen_Image.style.width = "";
        Screen_Image.style.height = "";
        Screen_Image.style.objectFit = "contain";
    }
}

function Update_Scale_Buttons() {
    if (!Scale_Bar) {
        return;
    }
    var Buttons = Scale_Bar.querySelectorAll(".Scale_Button");
    for (var I = 0; I < Buttons.length; I++) {
        var Btn = Buttons[I];
        var Mode = Btn.getAttribute("data-mode");
        if (Mode === Scale_Mode) {
            Btn.classList.add("Scale_Button_Active");
        } else {
            Btn.classList.remove("Scale_Button_Active");
        }
    }
}

function Set_Scale_Mode(Mode) {
    if (Mode !== "fit" && Mode !== "actual" && Mode !== "stretch") {
        return;
    }
    Scale_Mode = Mode;
    Apply_Scale_Mode();
    Update_Scale_Buttons();
    Schedule_Side_Bar_Auto_Close();
}

function Handle_Mouse_Event(E) {
    if (!Login_Authenticated || !Control_Active) {
        return;
    }
    if (!Web_Socket || Web_Socket.readyState !== WebSocket.OPEN) {
        return;
    }
    var Rect = Screen_Image.getBoundingClientRect();
    var X = (E.clientX - Rect.left) / Rect.width;
    var Y = (E.clientY - Rect.top) / Rect.height;
    if (X < 0) X = 0;
    if (Y < 0) Y = 0;
    if (X > 1) X = 1;
    if (Y > 1) Y = 1;
    var Button = "left";
    if (E.button === 2) {
        Button = "right";
    }
    var Action = null;
    if (E.type === "mousedown") {
        Action = "down";
        Mouse_Is_Down = true;
        Mouse_Button = Button;
        Keyboard_Active = true;
    } else if (E.type === "mouseup") {
        Action = "up";
        Mouse_Is_Down = false;
    } else {
        return;
    }
    var Msg = {
        Type: "Click",
        X: X,
        Y: Y,
        Button: Button,
        Action: Action
    };
    Web_Socket.send(JSON.stringify(Msg));
    E.preventDefault();
}

function Handle_Mouse_Move(E) {
    if (!Login_Authenticated || !Control_Active) {
        return;
    }
    if (!Web_Socket || Web_Socket.readyState !== WebSocket.OPEN) {
        return;
    }
    var Rect = Screen_Image.getBoundingClientRect();
    var X = (E.clientX - Rect.left) / Rect.width;
    var Y = (E.clientY - Rect.top) / Rect.height;
    if (X < 0) X = 0;
    if (Y < 0) Y = 0;
    if (X > 1) X = 1;
    if (Y > 1) Y = 1;
    var Msg = {
        Type: "Click",
        X: X,
        Y: Y,
        Button: Mouse_Button,
        Action: "move"
    };
    Web_Socket.send(JSON.stringify(Msg));
    E.preventDefault();
}

function Handle_Key_Down(E) {
    if (!Login_Authenticated || !Control_Active) {
        return;
    }
    if (!Keyboard_Active) {
        return;
    }
    if (!Web_Socket || Web_Socket.readyState !== WebSocket.OPEN) {
        return;
    }
    if (E.repeat) {
        return;
    }
    var Msg = {
        Type: "Key",
        Action: "down",
        Key: E.key,
        Code: E.code,
        Key_Code: E.keyCode
    };
    Web_Socket.send(JSON.stringify(Msg));
    E.preventDefault();
}

function Handle_Key_Up(E) {
    if (!Login_Authenticated || !Control_Active) {
        return;
    }
    if (!Web_Socket || Web_Socket.readyState !== WebSocket.OPEN) {
        return;
    }
    var Msg = {
        Type: "Key",
        Action: "up",
        Key: E.key,
        Code: E.code,
        Key_Code: E.keyCode
    };
    Web_Socket.send(JSON.stringify(Msg));
    E.preventDefault();
}

function Send_Key_Combo(Keys) {
    if (!Login_Authenticated || !Control_Active) {
        return;
    }
    if (!Web_Socket || Web_Socket.readyState !== WebSocket.OPEN) {
        return;
    }
    if (!Keys || !Keys.length) {
        return;
    }
    var Msg = {
        Type: "Key_Combo",
        Keys: Keys
    };
    Web_Socket.send(JSON.stringify(Msg));
}

function Set_Fullscreen_State(State) {
    Fullscreen_Active = State ? true : false;
    if (!Fullscreen_Button) {
        return;
    }
    if (Fullscreen_Active) {
        Fullscreen_Button.textContent = "Exit";
    } else {
        Fullscreen_Button.textContent = "Enter";
    }
}

function Toggle_Fullscreen(E) {
    var Doc = document;
    var Root = Doc.documentElement;
    if (!Fullscreen_Active) {
        if (Root.requestFullscreen) {
            Root.requestFullscreen();
        } else if (Root.webkitRequestFullscreen) {
            Root.webkitRequestFullscreen();
        } else if (Root.mozRequestFullScreen) {
            Root.mozRequestFullScreen();
        } else if (Root.msRequestFullscreen) {
            Root.msRequestFullscreen();
        }
    } else {
        if (Doc.exitFullscreen) {
            Doc.exitFullscreen();
        } else if (Doc.webkitExitFullscreen) {
            Doc.webkitExitFullscreen();
        } else if (Doc.mozCancelFullScreen) {
            Doc.mozCancelFullScreen();
        } else if (Doc.msExitFullscreen) {
            Doc.msExitFullscreen();
        }
    }
    Schedule_Side_Bar_Auto_Close();
    if (E) {
        E.preventDefault();
    }
}

function Handle_Fullscreen_Change(E) {
    var Doc = document;
    var Active = !!(Doc.fullscreenElement || Doc.webkitFullscreenElement || Doc.mozFullScreenElement || Doc.msFullscreenElement);
    Set_Fullscreen_State(Active);
}

function Start_Side_Bar_Drag(E) {
    if (!Side_Bar) {
        return;
    }
    if (E.button !== 0) {
        return;
    }
    var Rect = Side_Bar.getBoundingClientRect();
    Side_Bar_Dragging = true;
    Side_Bar_Did_Drag = false;
    Side_Bar_Drag_Offset_X = E.clientX - Rect.left;
    Side_Bar_Drag_Offset_Y = E.clientY - Rect.top;
    Side_Bar_Last_Mouse_X = E.clientX;
    Side_Bar_Last_Mouse_Y = E.clientY;
    E.preventDefault();
}

function Handle_Side_Bar_Drag(E) {
    if (!Side_Bar_Dragging || !Side_Bar) {
        return;
    }
    var Dx = E.clientX - Side_Bar_Last_Mouse_X;
    var Dy = E.clientY - Side_Bar_Last_Mouse_Y;
    if (Math.abs(Dx) > 2 || Math.abs(Dy) > 2) {
        Side_Bar_Did_Drag = true;
    }
    var New_Left = E.clientX - Side_Bar_Drag_Offset_X;
    var New_Top = E.clientY - Side_Bar_Drag_Offset_Y;
    var Max_Left = window.innerWidth - Side_Bar.offsetWidth;
    var Max_Top = window.innerHeight - Side_Bar.offsetHeight;
    if (New_Left < 0) New_Left = 0;
    if (New_Top < 0) New_Top = 0;
    if (New_Left > Max_Left) New_Left = Max_Left;
    if (New_Top > Max_Top) New_Top = Max_Top;
    Side_Bar.style.left = New_Left + "px";
    Side_Bar.style.top = New_Top + "px";
}

function Stop_Side_Bar_Drag(E) {
    Side_Bar_Dragging = false;
}

function Show_Key_Combo_Popup() {
    if (!Key_Combo_Popup) {
        return;
    }
    Key_Combo_Popup.style.display = "block";
}

function Hide_Key_Combo_Popup() {
    if (!Key_Combo_Popup) {
        return;
    }
    Key_Combo_Popup.style.display = "none";
}

function Toggle_Key_Combo_Popup(E) {
    if (!Key_Combo_Popup) {
        return;
    }
    if (Key_Combo_Popup.style.display === "block") {
        Key_Combo_Popup.style.display = "none";
    } else {
        Key_Combo_Popup.style.display = "block";
    }
    Schedule_Side_Bar_Auto_Close();
    if (E) {
        E.preventDefault();
    }
}

function Start_Key_Combo_Drag(E) {
    if (!Key_Combo_Popup) {
        return;
    }
    if (E.button !== 0) {
        return;
    }
    var Rect = Key_Combo_Popup.getBoundingClientRect();
    Key_Combo_Dragging = true;
    Key_Combo_Drag_Offset_X = E.clientX - Rect.left;
    Key_Combo_Drag_Offset_Y = E.clientY - Rect.top;
    Key_Combo_Last_Mouse_X = E.clientX;
    Key_Combo_Last_Mouse_Y = E.clientY;
    E.preventDefault();
}

function Handle_Key_Combo_Drag(E) {
    if (!Key_Combo_Dragging || !Key_Combo_Popup) {
        return;
    }
    var New_Left = E.clientX - Key_Combo_Drag_Offset_X;
    var New_Top = E.clientY - Key_Combo_Drag_Offset_Y;
    var Max_Left = window.innerWidth - Key_Combo_Popup.offsetWidth;
    var Max_Top = window.innerHeight - Key_Combo_Popup.offsetHeight;
    if (New_Left < 0) New_Left = 0;
    if (New_Top < 0) New_Top = 0;
    if (New_Left > Max_Left) New_Left = Max_Left;
    if (New_Top > Max_Top) New_Top = Max_Top;
    Key_Combo_Popup.style.left = New_Left + "px";
    Key_Combo_Popup.style.top = New_Top + "px";
}

function Stop_Key_Combo_Drag(E) {
    Key_Combo_Dragging = false;
}

function Parse_Keys_Text(Keys_Text) {
    var Parts = (Keys_Text || "").split("+");
    var Keys = [];
    for (var Index_Key = 0; Index_Key < Parts.length; Index_Key++) {
        var Part_Text = Parts[Index_Key].trim().toLowerCase();
        if (Part_Text) {
            Keys.push(Part_Text);
        }
    }
    return Keys;
}

function Submit_Login(E) {
    if (E) {
        E.preventDefault();
    }
    var U = Login_User ? Login_User.value.trim() : "";
    var P = Login_Password ? Login_Password.value : "";
    if (!U || !P) {
        if (Login_Error) {
            Login_Error.textContent = "Enter username and password";
        }
        return;
    }
    var P_Hash = Md5_Hash_Text(P);
    P = "";
    if (!Web_Socket || Web_Socket.readyState === WebSocket.CLOSING || Web_Socket.readyState === WebSocket.CLOSED) {
        if (Login_Error) {
            Login_Error.textContent = "Not connected to server";
        }
        return;
    }
    if (Web_Socket.readyState === WebSocket.CONNECTING) {
        if (Login_Error) {
            Login_Error.textContent = "Connecting...";
        }
        var Login_Open_Handler = function(Event) {
            Web_Socket.removeEventListener("open", Login_Open_Handler);
            if (Login_Error) {
                Login_Error.textContent = "";
            }
            var Msg_Inner = { Type: "Login", User: U, Password: P_Hash };
            Web_Socket.send(JSON.stringify(Msg_Inner));
        };
        Web_Socket.addEventListener("open", Login_Open_Handler);
        return;
    }
    if (Web_Socket.readyState !== WebSocket.OPEN) {
        if (Login_Error) {
            Login_Error.textContent = "Not connected to server";
        }
        return;
    }
    if (Login_Error) {
        Login_Error.textContent = "";
    }
    var Msg_Final = { Type: "Login", User: U, Password: P_Hash };
    Web_Socket.send(JSON.stringify(Msg_Final));
}

function Handle_Login_Result(Msg) {
    if (!Msg.Success) {
        if (Login_Error) {
            Login_Error.textContent = Msg.Error || "Login failed";
        }
        return;
    }
    Login_Authenticated = true;
    Control_Allowed = !!Msg.Control;
    Control_Active = false;
    Current_User_Name = Login_User ? Login_User.value.trim() : "";
    if (Login_Overlay) {
        Login_Overlay.style.display = "none";
    }
    if (User_Label) {
        User_Label.textContent = Current_User_Name;
    }
    Clear_Control_Message();
    Hide_Force_Control_Prompt();
    Update_Control_UI();
    var Hello_Msg = { Type: "Hello" };
    if (Web_Socket && Web_Socket.readyState === WebSocket.OPEN) {
        Web_Socket.send(JSON.stringify(Hello_Msg));
    }
}

function Request_Control(Force_Flag) {
    if (!Login_Authenticated) {
        return;
    }
    if (!Control_Allowed) {
        return;
    }
    if (!Web_Socket || Web_Socket.readyState !== WebSocket.OPEN) {
        return;
    }
    var Msg = { Type: "Control_Request", Force: Force_Flag ? true : false };
    Web_Socket.send(JSON.stringify(Msg));
}

function Send_Control_Release() {
    if (!Login_Authenticated) {
        return;
    }
    if (!Control_Allowed) {
        return;
    }
    if (!Web_Socket || Web_Socket.readyState !== WebSocket.OPEN) {
        return;
    }
    var Msg = { Type: "Control_Release" };
    Web_Socket.send(JSON.stringify(Msg));
}

function Clear_Control_Message() {
    if (Control_Message) {
        Control_Message.textContent = "";
        Control_Message.style.display = "none";
    }
}

function Show_Force_Control_Prompt(Controller_Name) {
    Force_Control_Pending = true;
    if (Control_Button) {
        Control_Button.style.display = "none";
    }
    if (Control_Message) {
        var Name = Controller_Name || "another user";
        Control_Message.textContent = "Control in use by " + Name + ". Force control?";
        Control_Message.style.display = "block";
    }
    if (Control_Confirm_Bar) {
        Control_Confirm_Bar.style.display = "flex";
    }
}

function Hide_Force_Control_Prompt() {
    Force_Control_Pending = false;
    if (Control_Confirm_Bar) {
        Control_Confirm_Bar.style.display = "none";
    }
    if (Control_Button && Control_Allowed) {
        Control_Button.style.display = "block";
    }
}

function Show_Control_Taken_Message(Controller_Name) {
    Force_Control_Pending = false;
    if (Control_Confirm_Bar) {
        Control_Confirm_Bar.style.display = "none";
    }
    if (Control_Button && Control_Allowed) {
        Control_Button.style.display = "block";
    }
    if (Control_Message) {
        var Name = Controller_Name || "another user";
        Control_Message.textContent = "Control taken by " + Name;
        Control_Message.style.display = "block";
    }
}

function Handle_Control_Result(Msg) {
    if (!Msg.Success) {
        if (Msg.In_Use) {
            var Name = Msg.Controller || "another user";
            Show_Force_Control_Prompt(Name);
        } else if (Msg.Error) {
            if (Control_Message) {
                Control_Message.textContent = Msg.Error;
                Control_Message.style.display = "block";
            }
        }
        return;
    }
    Control_Active = !!Msg.Active;
    Hide_Force_Control_Prompt();
    Clear_Control_Message();
    Update_Control_UI();
}

function Handle_Control_Changed(Msg) {
    var Was_Active = Control_Active ? true : false;
    Control_Active = !!Msg.Active;
    Update_Control_UI();
    if (Was_Active && !Control_Active) {
        var Name = Msg.Controller || "";
        if (Name && Name !== Current_User_Name) {
            Show_Control_Taken_Message(Name);
        }
    }
    if (Control_Active) {
        Clear_Control_Message();
    }
}

function Update_Control_UI() {
    var Keys_Section = document.getElementById("Keys_Section");
    if (!Control_Allowed) {
        if (Control_Button) {
            Control_Button.style.display = "none";
        }
        if (Keys_Section) {
            Keys_Section.style.display = "none";
        }
        return;
    }
    if (Control_Button) {
        if (Force_Control_Pending) {
            Control_Button.style.display = "none";
        } else {
            Control_Button.style.display = "block";
            if (Control_Active) {
                Control_Button.textContent = "Release Control";
                Control_Button.disabled = false;
            } else {
                Control_Button.textContent = "Take Control";
                Control_Button.disabled = false;
            }
        }
    }
    if (Keys_Section) {
        Keys_Section.style.display = "";
    }
}

function Handle_Control_Button_Click(E) {
    if (E) {
        E.preventDefault();
    }
    if (!Control_Allowed) {
        return;
    }
    if (Control_Active) {
        Send_Control_Release();
    } else {
        Request_Control(false);
    }
}

function Handle_Logout_Click(E) {
    if (E) {
        E.preventDefault();
    }
    if (Web_Socket) {
        try {
            Web_Socket.close();
        } catch (X) {}
    }
    window.location.reload();
}

function Start_Web_VNC() {
    Screen_Image = document.getElementById("Screen_Image");
    Monitor_Bar = document.getElementById("Monitor_Bar");
    Side_Bar = document.getElementById("Side_Bar");
    Side_Bar_Header = document.getElementById("Side_Bar_Header");
    Side_Toggle_Circle = document.getElementById("Side_Toggle_Circle");
    Scale_Bar = document.getElementById("Scale_Bar");
    Fullscreen_Button = document.getElementById("Fullscreen_Button");
    Key_Combo_Popup = document.getElementById("Key_Combo_Popup");
    Key_Combo_Header = document.getElementById("Key_Combo_Header");
    Key_Combo_Close = document.getElementById("Key_Combo_Close");
    Key_Combos_Button = document.getElementById("Key_Combos_Button");
    Login_Overlay = document.getElementById("Login_Overlay");
    Login_User = document.getElementById("Login_User");
    Login_Password = document.getElementById("Login_Password");
    Login_Button = document.getElementById("Login_Button");
    Login_Error = document.getElementById("Login_Error");
    User_Label = document.getElementById("User_Label");
    Control_Button = document.getElementById("Control_Button");
    Control_Message = document.getElementById("Control_Message");
    Control_Confirm_Bar = document.getElementById("Control_Confirm_Bar");
    Control_Yes_Button = document.getElementById("Control_Yes_Button");
    Control_No_Button = document.getElementById("Control_No_Button");
    Logout_Button = document.getElementById("Logout_Button");
    var Basic_Combos_Bar = document.getElementById("Basic_Combos_Bar");

    if (Side_Toggle_Circle) {
        Side_Toggle_Circle.addEventListener("mouseenter", Open_Side_Bar_On_Hover);
    }
    if (Side_Bar) {
        Side_Bar.addEventListener("mouseenter", function(E) {
            Side_Bar_Is_Hover = true;
        });
        Side_Bar.addEventListener("mouseleave", function(E) {
            Side_Bar_Is_Hover = false;
            Schedule_Side_Bar_Auto_Close();
        });
    }
    if (Side_Bar_Header) {
        Side_Bar_Header.addEventListener("mousedown", Start_Side_Bar_Drag);
    }
    window.addEventListener("mousemove", Handle_Side_Bar_Drag);
    window.addEventListener("mouseup", Stop_Side_Bar_Drag);

    if (Scale_Bar) {
        var Buttons = Scale_Bar.querySelectorAll(".Scale_Button");
        for (var Index_Btn = 0; Index_Btn < Buttons.length; Index_Btn++) {
            (function(Btn) {
                Btn.addEventListener("click", function(E) {
                    var Mode = Btn.getAttribute("data-mode");
                    Set_Scale_Mode(Mode);
                    E.preventDefault();
                });
            })(Buttons[Index_Btn]);
        }
    }

    if (Fullscreen_Button) {
        Fullscreen_Button.addEventListener("click", Toggle_Fullscreen);
    }
    if (Key_Combos_Button) {
        Key_Combos_Button.addEventListener("click", Toggle_Key_Combo_Popup);
    }
    if (Key_Combo_Close) {
        Key_Combo_Close.addEventListener("click", function(E) {
            Hide_Key_Combo_Popup();
            E.preventDefault();
        });
    }
    if (Key_Combo_Header) {
        Key_Combo_Header.addEventListener("mousedown", Start_Key_Combo_Drag);
    }
    window.addEventListener("mousemove", Handle_Key_Combo_Drag);
    window.addEventListener("mouseup", Stop_Key_Combo_Drag);

    var Combo_Buttons = document.querySelectorAll(".Key_Combo_Button");
    for (var Index_Combo = 0; Index_Combo < Combo_Buttons.length; Index_Combo++) {
        (function(Btn) {
            Btn.addEventListener("click", function(E) {
                var Keys_Text = Btn.getAttribute("data-keys") || "";
                var Keys = Parse_Keys_Text(Keys_Text);
                Send_Key_Combo(Keys);
                E.preventDefault();
            });
        })(Combo_Buttons[Index_Combo]);
    }

    if (Basic_Combos_Bar) {
        var Basic_Buttons = Basic_Combos_Bar.querySelectorAll(".Combo_Icon_Button");
        for (var Index_Basic = 0; Index_Basic < Basic_Buttons.length; Index_Basic++) {
            (function(Btn) {
                Btn.addEventListener("click", function(E) {
                    var Keys_Text = Btn.getAttribute("data-keys") || "";
                    var Keys = Parse_Keys_Text(Keys_Text);
                    Send_Key_Combo(Keys);
                    E.preventDefault();
                });
            })(Basic_Buttons[Index_Basic]);
        }
    }

    if (Login_Button) {
        Login_Button.addEventListener("click", Submit_Login);
    }
    if (Login_Password) {
        Login_Password.addEventListener("keydown", function(E) {
            if (E.key === "Enter") {
                Submit_Login(E);
            }
        });
    }

    if (Control_Button) {
        Control_Button.addEventListener("click", Handle_Control_Button_Click);
    }
    if (Logout_Button) {
        Logout_Button.addEventListener("click", Handle_Logout_Click);
    }
    if (Control_Yes_Button) {
        Control_Yes_Button.addEventListener("click", function(E) {
            if (E) {
                E.preventDefault();
            }
            if (!Login_Authenticated || !Control_Allowed) {
                Hide_Force_Control_Prompt();
                return;
            }
            Request_Control(true);
        });
    }
    if (Control_No_Button) {
        Control_No_Button.addEventListener("click", function(E) {
            if (E) {
                E.preventDefault();
            }
            Hide_Force_Control_Prompt();
            Clear_Control_Message();
        });
    }

    document.addEventListener("fullscreenchange", Handle_Fullscreen_Change);
    document.addEventListener("webkitfullscreenchange", Handle_Fullscreen_Change);
    document.addEventListener("mozfullscreenchange", Handle_Fullscreen_Change);
    document.addEventListener("MSFullscreenChange", Handle_Fullscreen_Change);

    Screen_Image.addEventListener("mousedown", Handle_Mouse_Event);
    Screen_Image.addEventListener("mouseup", Handle_Mouse_Event);
    Screen_Image.addEventListener("mousemove", Handle_Mouse_Move);
    Screen_Image.addEventListener("contextmenu", function(E) { E.preventDefault(); });

    window.addEventListener("keydown", Handle_Key_Down);
    window.addEventListener("keyup", Handle_Key_Up);
    window.addEventListener("blur", function(E) {
        Keyboard_Active = false;
    });

    Apply_Scale_Mode();
    Update_Scale_Buttons();
    Refresh_Monitors();
    Set_Fullscreen_State(false);
    Start_Connection();
}

window.addEventListener("load", Start_Web_VNC);
</script>
</body>
</html>
"""
        Html = Html.replace("VNC_Port_HERE", str(self._VNC_Port))
        return Html
        

class Silent_Handle(CGIHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    CORS_Origin = None
    Cache_Control = None
    Timeout = None
    Health_Path = None
    Allowed_Methods = None
    
    def log_message(self, Format, *Args):
        pass
        
    def log_error(self, Format, *Args):
        pass
        
    def copyfile(self, Source, Outputfile):
        try:
            shutil.copyfileobj(Source, Outputfile)
        except (BrokenPipeError, ConnectionResetError):
            pass
            
    def end_headers(self):
        if self.CORS_Origin is not None:
            self.send_header("Access-Control-Allow-Origin", self.CORS_Origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
        if self.Cache_Control is not None:
            self.send_header("Cache-Control", self.Cache_Control)
        super().end_headers()
        
    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()
        
    def do_GET(self):
        if self.Health_Path and self.path == self.Health_Path:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            try:
                self.wfile.write(b"OK")
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if self.Allowed_Methods and "GET" not in self.Allowed_Methods:
            self.send_response(405)
            self.end_headers()
            return
        try:
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            pass
            
    def do_HEAD(self):
        if self.Allowed_Methods and "HEAD" not in self.Allowed_Methods:
            self.send_response(405)
            self.end_headers()
            return
        try:
            super().do_HEAD()
        except (BrokenPipeError, ConnectionResetError):
            pass
            
    def do_POST(self):
        if self.Allowed_Methods and "POST" not in self.Allowed_Methods:
            self.send_response(405)
            self.end_headers()
            return
        try:
            super().do_POST()
        except (BrokenPipeError, ConnectionResetError):
            pass
            
    def setup(self):
        super().setup()
        try:
            if self.Timeout is not None:
                self.connection.settimeout(self.Timeout)
        except Exception:
            pass

class Quiet_Threading_Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    
    def __init__(self, Server_Address, Request_Handler_Class):
        super().__init__(Server_Address, Request_Handler_Class)
        self._Ip_Allowlist = None
        
    def verify_request(self, Request, Client_Address):
        if self._Ip_Allowlist is None:
            return True
        return Client_Address[0] in self._Ip_Allowlist
        
    def handle_error(self, Request, Client_Address):
        pass

class HTTP:
                
    def __str__(self):
        return f"HTTP[]"

    def __repr__(self):
        return f"HTTP[]"

    def __dir__(self):
        return []

    @property
    def __dict__(self):
        return {}
    
    class Server:
        
        def __init__(self, Port, IP=""):
            self._IP = IP
            self._Port = Port
            self._Handler = Silent_Handle
            self._Server_Class = Quiet_Threading_Server
            self._Received = False
            self._CGI_Paths = []
            self._Server = None
            self._CORS_Origin = None
            self._Cache_Control = None
            self._Timeout = None
            self._Health_Path = None
            self._Allowed_Methods = None
            self._IP_Allowlist = None
            self._TLS_Certfile = None
            self._TLS_Keyfile = None

        def __str__(self):
            return f"HTTP_Server[Port:{self._Port}, IP:{self._IP}]"

        def __repr__(self):
            return f"HTTP_Server[Port:{self._Port}, IP:{self._IP}]"

        def __dir__(self):
            return []

        @property
        def __dict__(self):
            return {}
            
        def Root(self, Path):
            os.chdir(Path)
            return True
            
        def CGI(self, Path):
            if not Path.startswith("/"):
                Path = "/" + Path
            if Path not in self._CGI_Paths:
                self._CGI_Paths.append(Path)
            self._Handler.cgi_directories = list(self._CGI_Paths)
            return True
            
        def CORS(self, Origin):
            self._CORS_Origin = Origin
            self._Handler.CORS_Origin = Origin
            return True
            
        def Cache_Control(self, Value):
            self._Cache_Control = Value
            self._Handler.Cache_Control = Value
            return True
            
        def Timeout(self, Seconds):
            self._Timeout = Seconds
            self._Handler.Timeout = Seconds
            return True
            
        def Allowed_Methods(self, Methods):
            self._Allowed_Methods = list(Methods) if Methods else None
            self._Handler.Allowed_Methods = self._Allowed_Methods
            return True
            
        def Health(self, Path):
            self._Health_Path = Path
            self._Handler.Health_Path = Path
            return True
            
        def Allow_Only_IPs(self, Ips):
            self._IP_Allowlist = list(Ips) if Ips else None
            if self._Server is not None:
                self._Server._IP_Allowlist = self._IP_Allowlist
            return True
            
        def Enable_TLS(self, Certfile, Keyfile=None):
            self._TLS_Certfile = Certfile
            self._TLS_Keyfile = Keyfile
            return True
            
        def Start(self):
            self._Handler.CORS_Origin = self._CORS_Origin
            self._Handler.Cache_Control = self._Cache_Control
            self._Handler.Timeout = self._Timeout
            self._Handler.Health_Path = self._Health_Path
            self._Handler.Allowed_Methods = self._Allowed_Methods
            self._Server = self._Server_Class((self._IP, self._Port), self._Handler)
            self._Server._IP_Allowlist = self._IP_Allowlist
            if self._TLS_Certfile is not None:
                Context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                Context.load_cert_chain(self._TLS_Certfile, keyfile=self._TLS_Keyfile)
                self._Server.socket = Context.wrap_socket(self._Server.socket, server_side=True)
            self._Server.serve_forever()
            
        def Close(self):
            if self._Server is not None:
                self._Server.shutdown()
                self._Server.server_close()
                self._Server = None

    class Socket:
        
        def __init__(self, Port, IP="", Timeout=30):
            self._IP = IP
            self._Port = Port
            self._Timeout = Timeout
            self._Server_Socket = None
            self._Clients = set()

        def __str__(self):
            return f"HTTP_Socket[Port:{self._Port}, IP:{self._IP}]"

        def __repr__(self):
            return f"HTTP_Socket[Port:{self._Port}, IP:{self._IP}]"

        def __dir__(self):
            return []

        @property
        def __dict__(self):
            return {}

        def Start(self):
            Server_Socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            Server_Socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            Server_Socket.bind((self._IP, self._Port))
            Server_Socket.listen(100)
            self._Server_Socket = Server_Socket

        def Close(self):
            if self._Server_Socket:
                try:
                    self._Server_Socket.close()
                except Exception:
                    pass
                self._Server_Socket = None
            for Client in list(self._Clients):
                try:
                    Client.close()
                except Exception:
                    pass
            self._Clients = set()

        def Close_Client(self, Client):
            try:
                self._Send_Frame(Client, 0x8, b"")
            except Exception:
                pass
            try:
                Client.close()
            except Exception:
                pass
            try:
                self._Clients.discard(Client)
            except Exception:
                pass

        def Receive(self, Client=None):
            if self._Server_Socket is None:
                return None, None
            Select_Timeout = 0.1
            try:
                Sockets_List = [self._Server_Socket]
                if self._Clients:
                    Sockets_List.extend(self._Clients)
                Readable_Sockets, _, _ = select.select(Sockets_List, [], [], Select_Timeout)
            except Exception:
                return None, None
            if not Readable_Sockets:
                return None, None
            if self._Server_Socket in Readable_Sockets:
                try:
                    New_Client, Address = self._Server_Socket.accept()
                    New_Client.settimeout(self._Timeout)
                    if self._Handshake(New_Client):
                        self._Clients.add(New_Client)
                    else:
                        try:
                            New_Client.close()
                        except Exception:
                            pass
                except Exception:
                    pass
                Readable_Sockets = [S for S in Readable_Sockets if S is not self._Server_Socket]
                if not Readable_Sockets:
                    return None, None
            Selected_Client = None
            if Client in Readable_Sockets:
                Selected_Client = Client
            else:
                Selected_Client = Readable_Sockets[0]
            try:
                Frame = self._Decode_Frame(Selected_Client)
            except Exception:
                self.Close_Client(Selected_Client)
                return Selected_Client, None
            if Frame is None:
                self.Close_Client(Selected_Client)
                return Selected_Client, None
            Op_Code, Data = Frame
            if Op_Code == 0x9:
                try:
                    self._Send_Frame(Selected_Client, 0xA, Data)
                except Exception:
                    self.Close_Client(Selected_Client)
                    return Selected_Client, None
                return None, None
            if Op_Code == 0x8:
                self.Close_Client(Selected_Client)
                return Selected_Client, None
            if Op_Code == 0x1:
                try:
                    return Selected_Client, Data.decode("utf-8", "replace")
                except Exception:
                    return Selected_Client, None
            if Op_Code == 0x2:
                return Selected_Client, Data
            return Selected_Client, None

        def Send(self, Client, Reply):
            if isinstance(Reply, (bytes, bytearray)):
                try:
                    self._Send_Frame(Client, 0x2, bytes(Reply))
                    return True
                except Exception:
                    self.Close_Client(Client)
                    return False
            else:
                try:
                    self._Send_Frame(Client, 0x1, str(Reply).encode("utf-8"))
                    return True
                except Exception:
                    self.Close_Client(Client)
                    return False

        def _Handshake(self, Client):
            Request_Raw = b""
            while b"\r\n\r\n" not in Request_Raw:
                Chunk = Client.recv(2048)
                if not Chunk:
                    return False
                Request_Raw += Chunk
            try:
                Lines = Request_Raw.decode("iso-8859-1").split("\r\n")
                Headers = {}
                for Line in Lines[1:]:
                    if not Line:
                        continue
                    if ":" in Line:
                        Key, Value = Line.split(":", 1)
                        Headers[Key.strip().lower()] = Value.strip()
                Client_Key = Headers.get("sec-websocket-key")
                if not Client_Key:
                    return False
                Accept_Key = base64.b64encode(hashlib.sha1((Client_Key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
                Response = (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Accept: " + Accept_Key + "\r\n"
                    "\r\n"
                )
                Client.sendall(Response.encode("ascii"))
                return True
            except Exception:
                return False

        def _Recv_Exact(self, Client, Count):
            Data = b""
            while len(Data) < Count:
                Chunk = Client.recv(Count - len(Data))
                if not Chunk:
                    return None
                Data += Chunk
            return Data

        def _Decode_Frame(self, Client):
            Header = self._Recv_Exact(Client, 2)
            if not Header:
                return None
            Byte1, Byte2 = Header[0], Header[1]
            Op = Byte1 & 0x0F
            Length = Byte2 & 0x7F
            if Length == 126:
                Ext = self._Recv_Exact(Client, 2)
                if not Ext:
                    return None
                Length = struct.unpack("!H", Ext)[0]
            elif Length == 127:
                Ext = self._Recv_Exact(Client, 8)
                if not Ext:
                    return None
                Length = struct.unpack("!Q", Ext)[0]
            Mask = b""
            if (Byte2 >> 7) & 1:
                Mask = self._Recv_Exact(Client, 4)
                if not Mask:
                    return None
            Payload = self._Recv_Exact(Client, Length)
            if Payload is None:
                return None
            if Mask:
                Payload = bytes(B ^ Mask[i % 4] for i, B in enumerate(Payload))
            return (Op, Payload)

        def _Send_Frame(self, Client, Op, Data):
            Byte1 = 0x80 | (Op & 0x0F)
            Length = len(Data)
            if Length < 126:
                Header = struct.pack("!BB", Byte1, Length)
            elif Length < (1 << 16):
                Header = struct.pack("!BBH", Byte1, 126, Length)
            else:
                Header = struct.pack("!BBQ", Byte1, 127, Length)
            Client.sendall(Header + Data)