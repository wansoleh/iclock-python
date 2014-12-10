#!/usr/bin/python
# -*- coding: utf-8 -*-
from mysite.iclock.models import * 
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render_to_response
from django.core.exceptions import ObjectDoesNotExist
from django.core.cache import cache
import string
import datetime
import time, os, sys
from mysite.iclock.dataproc import getFW
from django.conf import settings
from mysite.iclock.dataproc import appendDevCmd, devUpdateFirmware
from mysite.utils import *
from django.db import connection as conn
import re
from django.utils.encoding import smart_str

ip4_re = re.compile(r'^(25[0-5]|2[0-4]\d|[0-1]?\d?\d)(\.(25[0-5]|2[0-4]\d|[0-1]?\d?\d)){3}$')
sqlCount = 0

def index(request):
	if not request.user or request.user.is_anonymous():
		url = settings.LOGIN_URL
	else:
		url = settings.LOGIN_REDIRECT_URL
	return HttpResponseRedirect(url)

	global MCOUNT
	MCOUNT += 1;
#	time.sleep(10)
	return HttpResponse("This is the iclock device core communication, MCOUNT=%d, UPDATE_COUNT=%d, sqlCount=%d." % (MCOUNT, settings.UPDATE_COUNT, sqlCount))

def getEmployee(pin, Device=None):
	pin = formatPIN(pin)
	return employee.objByPIN(pin, Device)

def normalState(state):
	if state == '0':	return 'I'
	if state == '1':	return 'O'
	try:
		d = int(state)
		if d >= 32 and d < 127:
			return chr(d)
	except: pass
	return state

def normalVerify(state):
	try:
		d = int(state)
		if d >= 32 and d < 127:
			return chr(d)
	except: pass
	return state

server_time_delta = datetime.datetime.now() - datetime.datetime.utcnow()

#设备上传的考勤记录
def lineToLog(device, line):
	flds = string.split(line, "\t") + ["", "", "", "", "", "", ""]
	pin = flds[0]
	try:
		if pin in settings.DISABLED_PINS:	return None
	except:
		return None
	if flds[5] == '255' and flds[3] == '3' and flds[0] == flds[4]:
		return None
	pin = getEmployee(flds[0], device).id
	try:
		logtime = datetime.datetime.strptime(flds[1], "%Y-%m-%d %H:%M:%S")
	except:
		return None
	if (datetime.datetime.now() + datetime.timedelta(1, 0, 0)) < logtime: #时间比当前时间还要多一天
		return None
	att1=transaction.objects.filter(UserID=pin,TTime=logtime)
	if att1:
		#print"att1 not NULL"
		return None
	else:
		#print"att1 is NULL"
		sql = """insert into %s (userid, checktime, checktype, verifycode, SN, WorkCode, Reserved, sensorid) values('%s', '%s', '%s', '%s', '%s', '%s', '%s', '%s')""" % (transaction._meta.db_table, pin, logtime, normalState(flds[2]), normalVerify(flds[3]), device.SN, flds[4], flds[5], device.DeptID_id)
		return sql
#	if device.TZAdj <> None:
#		count_minutes = None
#		if abs(device.TZAdj)<=13:
#			count_minutes = device.TZAdj*3600
#		else:
#			count_minutes = device.TZAdj*60
#		logtime = logtime - datetime.timedelta(0, count_minutes) + server_time_delta #UTC TIM
#	sql = """insert into %s (userid, checktime, checktype, verifycode, SN, WorkCode, Reserved, sensorid) values('%s', '%s', '%s', '%s', '%s', '%s', '%s', '%s')""" % (transaction._meta.db_table, pin, logtime, normalState(flds[2]), normalVerify(flds[3]), device.SN, flds[4], flds[5], device.DeptID_id)
#	return sql

def cardToNum(card):
	if card and len(card) == 12 and card[0] == '[' and card[-3:] == '00]':
		try:
			card = "%s" % (int(card[1:3], 16) + int(card[3:5], 16) * 256 + int(card[5:7], 16) * 256 * 256 + int(card[7:9], 16) * 256 * 256 * 256)
		except:
			appendFile("ERROR(lineTocard - USER):%s\nITEMS:%s" % (e, card))
			return card
	return card

def lineToOpLog(cursor, device, flds):
	try: #0	0	2008-08-28 14:07:37	0	0	0	0
		flds = flds.split("\t")
		try:
			logtime = datetime.datetime.strptime(flds[2], "%Y-%m-%d %H:%M:%S")
		except:
			return None
		if (datetime.datetime.now() + datetime.timedelta(1, 0, 0)) < logtime: #时间比当前时间还要多一天
			return None
		if device.TZAdj <> None:
			logtime = logtime - datetime.timedelta(0, device.TZAdj * 60 * 60) + server_time_delta #UTC TIME, then server time
		obj = oplog(SN=device, admin=flds[1], OP=flds[0], OPTime=logtime,
				Object=flds[3], Param1=flds[4], Param2=flds[5], Param3=flds[6])
		obj.save()
	except:
		return None

#设备上传的数据命令处理
def lineToUser(cursor, device, line):
		try:
			if line.find("\tName=") > 0:
				#ops=unicode(line.encode("gb2312")).split(" ",1)
				ops = line.split(" ", 1)	
			else:
				ops = line.encode("ascii").split(" ", 1)					
		except Exception, e:
			appendFile("ERROR(lineToUser):%s\nOPS:%s" % (e, ops))
			ops = line.split(" ", 1)	
		if ops[0] == 'OPLOG': #by super 2010-08-12 操作日志 格式：OPLOG 操作代码 管理员ID 时间 操作对象1 ** 操作对象2 操作对象3 操作对象4==OPLOG 11        0       2010-08-12 10:29:00     11      0       0       0
			return lineToOpLog(cursor, device, ops[1])
		flds = {};
		for item in ops[1].split("\t"):
			index = item.find("=")
			if index > 0: flds[item[:index]] = item[index + 1:]
		try:
			pin = formatPIN(flds["PIN"])
			if int(pin) in settings.DISABLED_PINS:
				return
		except Exception, e:
			appendFile("ERROR(lineToUser - OPLOG):%s\nITEMS:%s" % (e, item))
			return
		backDev = device.BackupDevice()
		employee = getEmployee(pin, device)
		#by super 2010-08-16 将下发的用户信息保存到记事本
		#dt = str(datetime.datetime.now())[:10]
		#if ops[0]=="FP":dt="TMP"+dt
		#f = file('tmp\DevPostUser%s.txt'%dt,'a')
		#f.write(line + '\n')
		#f.close()
		if ops[0] == "USER":
			try:
				ename = flds["Name"].decode("utf-8")
#				ename=unicode(flds["Name"])[:40]
#				ename=flds["Name"]
				#if isinstance(ename, unicode):
				#	ename = ename.encode('gb2312')
			except Exception, e:
				ename = ' '		
				appendFile("ERROR(lineToUser - USER):%s\nITEMS:%s" % (e, flds["Name"]))
			passwd = flds["Passwd"]
			card = flds["Card"]
			agrp = flds["Grp"]
			tz = flds["TZ"]
			#priv = flds['Pri']
			fldNames = ['SN', 'utime']
			values = [device.SN, str(datetime.datetime.now())[:19]]
			try:
				if ename and (ename != employee.EName) or not ename:#by super 2010-08-12 若为空也允许修改用户名
					fldNames.append('name')
					values.append(ename)
					employee.EName = ename
				#if passwd and (passwd!=e.Password):
				if passwd and (passwd != employee.Password) or not passwd: #by super 2010-08-12 若为空也允许修改密码
					fldNames.append('password')
					values.append(passwd)
					employee.Password = passwd
				#if priv != employee.Privilege:
					#fldNames.append('privilege')
					#values.append(priv)
					#employee.Privilege = priv
				if card and (cardToNum(card) != employee.Card) or not card: #by super 2010-08-12 若为空也允许修改卡号
					fldNames.append('Card')
					values.append(cardToNum(card))
					employee.Card = cardToNum(card)
				if agrp != employee.AccGroup:
					fldNames.append('AccGroup')
					values.append(agrp)
					employee.AccGroup = agrp
				if tz != employee.TimeZones:
					fldNames.append('TimeZones')
					values.append(tz)
					employee.TimeZones = tz
			except:
				appendFile("ERROR(lineToUser - USER):%s\nITEMS:%s" % (e,"error"))
			cmdStr = "DATA UPDATE USERINFO Name=%s\tPri=%s\tPasswd=%s\tCard=%s\tGrp=%s\tTZ=%s" % (flds["PIN"],
				ename or "", passwd or "", card and ((card != "[0000000000]") and card or "") or "",
				agrp, tz and (tz != "00000000" and tz or "") or "")
			try:
				employee.IsNewEmp
			except Exception, e:
				appendFile("ERROR(lineToUser - USER - insert database):%s\nSQL:%s" % (e, cmdStr))
				employee.IsNewEmp = False
			if employee.IsNewEmp:
				if backDev:
					appendDevCmd(backDev, cmdStr, cursor)
				employee.IsNewEmp = False
				try:
					employee.save()
				except Exception, e:
					appendFile("ERROR(lineToUser - USER - save database)%s:%s\n" % (datetime.time, e))
				sql = ''
			elif len(fldNames) > 2: #有新的用户信息
				sql = u"update userinfo set %s where badgenumber='%s'" % (','.join([u"%s='%s'" % (fldNames[i], values[i]) for i in range(len(fldNames))]), pin)
				if backDev:
					appendDevCmd(backDev, cmdStr, cursor)
			else:
				sql = ''
			if sql:
				cursor.execute(sql)
			return employee
		elif ops[0] == "FP":
			if backDev:
				appendDevCmd(backDev, "DATA UPDATE USERINFO PIN=%s" % flds["PIN"], cursor)
			fp = trimTemp(flds["TMP"])
			try:
				#if fp and int(flds["Size"]) == len(fp): #判断长度是否一致 by super:
				fptemplate = fptemp.objects.get(UserID=employee.id, FingerID=flds["FID"])
				if fp[:100] == fptemplate.Template[:100]:
					pass # Template is same
				else:
					sql = "update template set template = '%s', SN='%s', utime='%s' where userid='%s' and fingerid=%s" % (fp, device.SN, str(datetime.datetime.now())[:19], employee.id, flds["FID"])
					if backDev:
						appendDevCmd(backDev, "DATA UPDATE FINGERTMP PIN=%s\tFID=%s\tSize=%s\tValid=1\tTMP=%s" % (flds["PIN"], flds["FID"], len(fp), fp), cursor) 
					cursor.execute(sql)
			except ObjectDoesNotExist:
				#if fp and int(flds["Size"]) == len(fp): #判断长度是否一致 by super:
				sql = "insert into template(template, userid, fingerid, SN, utime, valid, DelTag) values('%s', '%s', %s, '%s', '%s', 1, '0')" % (fp, employee.id, flds["FID"], device.SN, str(datetime.datetime.now())[:19])
				if backDev:
					appendDevCmd(backDev, "DATA UPDATE FINGERTMP PIN=%s\tFID=%s\tSize=%s\tValid=1\tTMP=%s" % (flds["PIN"], flds["FID"], len(fp), fp), cursor) 
				cursor.execute(sql)			
			return employee
		else:
			return False

up_version = fwVerStd(settings.UPGRADE_FWVERSION)

#检查正在请求的设备是否登记过
def checkDevice(request, response):
	#by super 2010-08-26 增加push sdk协议字段
	pushver = 0.0
	if request.REQUEST.has_key('pushver'):
		try:pushver = float(request.REQUEST["pushver"])
		except:pass

	try:
		sn = request.REQUEST["SN"]
	except:
		sn = request.META["QUERY_STRING"]
		sn = (sn + "SN=").split("SN=")[1].split("&")[0]
		if sn == "":
			errorLog(request)
			response.write("UNKNOWN Device")
			return None
	try:
		device = getDevice(sn)
		
		#by super 2010-08-26 增加push sdk协议字段,更新协议版本
		if float(device.PushVersion) != pushver:
			device.PushVersion = pushver
			device.save()
		
		if device.DelTag:
			if settings.ICLOCK_AUTO_REG:
				device.DelTag = None
				device.save()
			else:
				return None
		if device.State == DEV_STATUS_PAUSE:
			return None

	except:
		if len(sn) >= 6 and settings.ICLOCK_AUTO_REG and not request.REQUEST.has_key('PIN'): #若SN正确，设置可以自动注册，且没有查询用户信息的话，注册该考勤机
			device = iclock(SN=sn, Alias=request.META["REMOTE_ADDR"],
				LastActivity=datetime.datetime.now(), IPAddress=request.META["REMOTE_ADDR"], DeptID=getDefaultDept())
			
			#by super 2010-08-26 增加push sdk协议字段
			device.PushVersion = pushver
			
			device.save()
			device = iclock.objects.get(SN=sn)
			appendDevCmd(device, "INFO")
			appendDevCmd(device, "CHECK")
		else:
			errorLog(request)
			response.write("UNKNOWN Device: " + sn)
			return None
	return device

def trySql(cursor, sql):
	try:
		cursor.execute(sql)
		conn._commit();
	except:
		conn.close()
		cursor = conn.cursor()
		cursor.execute(sql)
		conn._commit();

def saveDeviceStamp(cursor, device, field, stamp):
	sql = "update iclock set %s='%s' where sn='%s'" % (field, stamp, device.SN)
	trySql(cursor, sql)

def checkAndSave(device): #更新设备的LastActivity，通过缓存比较的方法，避免频繁写数据库
	n = datetime.datetime.now()
	old = 0
	removeLastReboot(device.IPAddress)
	laKey = "iclock_la_" + device.SN  #保存最后一次更新数据库的LastActivity, 以便于下一次的比较
	if laKey in cache:
		old = cache.get(laKey)
	device.LastActivity = n
	cache.set("iclock_" + device.SN, device)
	if not old or ((n - old).seconds > 3): #只有LastActivity在间隔大于59秒的更新才反映到数据库中去
		try:
			cursor = conn.cursor()
			sql = "update iclock set lastactivity='%s' where sn='%s'" % (str(n)[:19], device.SN)
			cursor.execute(sql)
			conn._commit();
			device.save()
		except:
			device.save()
		cache.set(laKey, n)

def commitLog(cursor, sql, conn=None):
	global sqlCount
	if settings.DATABASE_ENGINE == 'oracle':
		sql = "begin " + sql + ";end;;"
#	if DATABASE_ENGINE == 'mysql':	sql="BEGIN\n"+sql+"\nEND"
	#cursor.execute(sql)
	#conn._commit()
	if type(sql) == type([]):
		for data in sql:
			try:
				cursor.execute(data)
			except Exception,e: 
				raise e
	elif type(sql) == type((0,)):
		cursor.execute(statement, sql)
	else:
		cursor.execute(sql)
	if conn: 
		conn._commit()
	sqlCount += 1

		

#by super 2010-08-25 修改向下兼容

def postDataToFile_BySuper(request, device, rawData):
	resp = ""
	now = datetime.datetime.now()
	old_file = "tmp\\read\\%s_%s" % (device.SN, now.strftime("%y%m%dT%H%M%S"))
	
	LogStamp = ""
	dataType = "OPLOG"
	if request.REQUEST.has_key('Stamp'): #and request.REQUEST.has_key('table'):
		if request.REQUEST.has_key('table'): #新协议
			LogStamp = request.REQUEST['Stamp']
			if request.REQUEST['table'] == 'ATTLOG':#by super 2010-07-22 上传考勤记录
				dataType = "TRANSACTIONS"
			elif request.REQUEST['table'] == 'OPERLOG':#by super 2010-07-22 上传用户信息和系统日志
				rawData = decode_data(rawData)
	elif request.REQUEST.has_key('OpStamp'):
		rawData = decode_data(rawData)
	else:
		return resp
		
	s_data = ":%s: SN=%s\tIP=%s\tTIME=%s\n%s\n\n" % (dataType, device.SN,
			real_ip(request),
			datetime.datetime.now(),
			rawData)
	if (request.REQUEST.has_key('table') and request.REQUEST['table'] == 'OPERLOG') or request.REQUEST.has_key('OpStamp'):
		s_data = s_data.encode("utf-8")
	#写入到队列，后台进程在进行实际的数据库写入操作
	try:
		write_to_file(old_file, s_data)
	except Exception, e:
		errorLog(request)
		raise e
	else:
		device.LogStamp = request.REQUEST['Stamp']
		device.OpLogStamp = request.REQUEST['OpStamp']
		resp = "OK:%d\n" % 1
	
#	s_data=":TRANSACTIONS: SN=%s\tIP=%s\tTIME=%s\n%s\n\n"%(device.SN,
#			real_ip(request), 
#			datetime.datetime.now(),
#			rawData)
#	#写入到队列，后台进程在进行实际的数据库写入操作
#	try:
#		write_to_file(old_file, s_data)
#		#q_server=queqe_server()
#		#q_server.lpush("TRANS", s_data)
#		#q_server.connection.disconnect()
#	except Exception, e:
#		#resp+=u"ERROR:%s\n"%e
#		print u"ERROR:%s\n"%e
#		raise e
#	else:
#		device.LogStamp=request.REQUEST['Stamp']
#		resp="OK:%d\n"%1
#	
#	s_data=u":OPLOG: SN=%s\tIP=%s\tTIME=%s\n%s\n\n"%(device.SN,
#			real_ip(request), 
#			datetime.datetime.now(),
#			decode_data(rawData))
#													
#	#写入到队列，后台进程在进行实际的数据库写入操作
#	try:
#		write_to_file(old_file, s_data.encode("utf-8")) 
#		#q_server=queqe_server()
#		#q_server.lpush("TRANS", s_data.encode("utf-8"))
#		#q_server.connection.disconnect()
#	except Exception, e:
#		#resp+=u"ERROR:%s\n"%e
#		errorLog(request)
#		raise e
#	else:
#		device.OpLogStamp=request.REQUEST['OpStamp']
#		resp="OK:%d\n"%1
		
	return resp

#设备主动向服务器发送的数据

def cdata(request):
	encrypt = 1
	response = HttpResponse(mimetype='text/plain')
	getResp = ""
	try:
		cursor = conn.cursor()
		resp = ""
		device = checkDevice(request, response)
		if(device == None): return response
		if request.REQUEST.has_key('action'):
			resp += "OK\n"
		elif request.method == 'GET':
			if request.REQUEST.has_key('PIN'):
				save = request.REQUEST.has_key('save') and (request.REQUEST['save'] in ['1', 'Y', 'y', 'yes', 'YES']) or False
				try:
					pin = request.REQUEST['PIN']
					emp = employee.objects.get(PIN=formatPIN(pin))
				except ObjectDoesNotExist:
					resp += "NONE"
				else:
					cc = u"DATA UPDATE USERINFO PIN=%s\tName=%s\tPasswd=%s\tGrp=%d\tCard=%s\tTZ=%s\tPri=%s\n" % (emp.pin(), emp.EName or "", emp.Password or "", emp.AccGroup or 1, getNormalCard(emp.Card), emp.TimeZones or "", save and emp.Privilege or 0)
					for fp in fptemp.objects.filter(UserID=emp):
						try:
							#cc+=u"DATA FP PIN=%s\tFID=%d\tTMP=%s\n"%(emp.pin(), fp.FingerID, fp.temp())
							cc += u"DATA UPDATE FINGERTMP PIN=%s\tFID=%d\tSize=%s\tTMP=%s\n" % (emp.pin(), fp.FingerID, len(fp.temp()), fp.temp()) #by super 2010-07-24
						except:pass
					try:
						resp += cc.encode("gb18030")
					except:
						resp += cc.decode("utf-8").encode("gb18030")
					if not save: # if not saved user in device, delete it after serveral(5) minutes
						endTime = datetime.datetime.now() + datetime.timedelta(0, 5 * 60)
						appendDevCmd(device, "DATA DELETE USERINFO PIN=%s" % emp.pin(), cursor, endTime)
			else:
				#by super 2010-08-25 协议向下兼容
				pushverValue = None
				if request.REQUEST.has_key('pushver'):
					pushverValue = request.REQUEST['pushver']				
				attlogStamp = "Stamp"
				operlogStamp = "OpStamp"
				attphotoStamp = "PhotoStamp"
				TransFlag = "0000000000"
				if pushverValue :#新协议
					attlogStamp = "ATTLOGStamp"
					operlogStamp = "OPERLOGStamp"
					attphotoStamp = "ATTPHOTOStamp"
					#TransFlag = device.UpdateDB.replace("\\t", "\t")						
					TransFlag = "1111000000"
				resp += "GET OPTION FROM: %s\n" % device.SN
				#resp+="Stamp=%s\n"%device.LogStamp
				#resp+="OpStamp=%s\n"%(device.OpLogStamp or 0)
				#resp+="PhotoStamp=%s\n"%(device.PhotoStamp or 0)
#				resp+="ATTLOGStamp=%s\n"%device.LogStamp
#				resp+="OPERLOGStamp=%s\n"%(device.OpLogStamp or 0)
#				resp+="ATTPHOTOStamp=%s\n"%(device.PhotoStamp or 0)
				resp += "%s=%s\n" % (attlogStamp, device.LogStamp)
				resp += "%s=%s\n" % (operlogStamp, device.OpLogStamp or 0)
				resp += "%s=%s\n" % (attphotoStamp, device.PhotoStamp or 0)
				resp += "ErrorDelay=%d\n" % max(30, settings.MIN_REQ_DELAY)
				resp += "Delay=%d\n" % settings.MIN_REQ_DELAY
				resp += "TransTimes=%s\n" % device.TransTimes
				resp += "TransInterval=%d\n" % max(settings.MIN_TRANSINTERVAL, device.TransInterval)
				resp += "TransFlag=%s\n" % device.UpdateDB.replace("\\t", "\t")
				#resp += "TransFlag=%s\n" % TransFlag#device.UpdateDB.replace("\\t","\t") #by super 2010-07-30
				try:
					if not (device.TZAdj == None):
						resp += "TimeZone=%s\n" % device.TZAdj
				except:
					pass 
				resp += "Realtime=%s\n" % settings.TRANS_REALTIME
				resp += "Encrypt=%s\n" % settings.ENCRYPT
				if pushverValue :
					resp += "ServerVer=0.0.2 2010-07-22\n"    #2010-09-17  新版本才有服务版本信息和表明时间戳
					resp += "TableNameStamp\n\n"
				encrypt = 0
			checkAndSave(device)
		elif request.method == 'POST':
			raw_Data = request.raw_post_data
			if settings.ENCRYPT:
				import lzo
				rawData = lzo.bufferDecrypt(raw_Data, device.SN)
			else:
				rawData = raw_Data
			#by super 2010-08-16 将下发的用户信息保存到记事本
			now = datetime.datetime.now()
			filepath = os.path.abspath(os.path.dirname(sys.argv[0]))
			old_file = "%s\\read\\%s_%s" % (tmpDir(), device.SN, now.strftime("%y%m%dT%H%M%S"))    
			#考勤
			if request.REQUEST.has_key('Stamp'): #and request.REQUEST.has_key('table'):
				if request.REQUEST.has_key('table'): #新协议				
					if request.REQUEST['table'] == 'ATTLOG':#by super 2010-07-22 上传考勤记录
						s_data = ":TRANSACTIONS: SN=%s\tIP=%s\tTIME=%s\n%s\n\n" % (device.SN,
								real_ip(request),
								datetime.datetime.now(),
								rawData)
						#写入到队列，后台进程在进行实际的数据库写入操作
						try:
							write_to_file(old_file, s_data.encode("utf-8"))
							#q_server=queqe_server()
							#q_server.lpush("TRANS", s_data)
							#q_server.connection.disconnect()
						except Exception, e:
							appendFile(e)
							resp += u"ERROR:%s\n" % e
							print u"ERROR:%s\n" % e
							raise e
						else:
							device.LogStamp = request.REQUEST['Stamp']
							resp += "OK:%d\n" % 1		
					elif request.REQUEST['table'] == 'OPERLOG':#by super 2010-07-22 上传用户信息和系统日志
						try:
							try:
								rawData.decode("ascii")
							except:
								rawData=rawData.decode("gb18030")
							s_data = u":OPLOG: SN=%s\tIP=%s\tTIME=%s\n%s\n\n" % (device.SN,
									real_ip(request),
									datetime.datetime.now(),
									rawData)
						except Exception, e:
							appendFile("--%s--%s" % (now, e))
							raise e
						#s_data = u":OPLOG: SN=%s\tIP=%s\tTIME=%s\n%s\n\n" % (device.SN,
								#real_ip(request),
								#datetime.datetime.now(),
								#rawData)
						#写入到队列，后台进程在进行实际的数据库写入操作
						try:
							write_to_file(old_file, s_data.encode("utf-8")) 
							print 'success to write file'
							#q_server=queqe_server()
							#q_server.lpush("TRANsS", s_data.encode("utf-8"))
							#q_server.connection.disconnect()
						except Exception, e:
							resp += u"ERROR:%s\n" % e
							appendFile(e)
							errorLog(request)
							raise e
						else:
							device.OpLogStamp = request.REQUEST['Stamp']
							resp += "OK:%d\n" % 1					
					elif request.REQUEST['table'] == 'ATTPHOTO':#by super 2010-07-24 上传照片
						return postPhoto(request)
				else:
					s_data = ":TRANSACTIONS: SN=%s\tIP=%s\tTIME=%s\n%s\n\n" % (device.SN,
							real_ip(request),
							datetime.datetime.now(),
							rawData)
					#写入到队列，后台进程在进行实际的数据库写入操作
					try:
						write_to_file(old_file, s_data.encode("utf-8"))
						#q_server=queqe_server()
						#q_server.lpush("TRANS", s_data)
						#q_server.connection.disconnect()
					except Exception, e:
						appendFile(e)
						resp += u"ERROR:%s\n" % e
						print u"ERROR:%s\n" % e
						raise e
					else:
						device.LogStamp = request.REQUEST['Stamp']
						resp += "OK:%d\n" % 1	
				
			elif request.REQUEST.has_key('OpStamp'):
				s_data = u":OPLOG: SN=%s\tIP=%s\tTIME=%s\n%s\n\n" % (device.SN,
						real_ip(request),
						datetime.datetime.now(),
						decode_data(rawData))

				#写入到队列，后台进程在进行实际的数据库写入操作
				try:
					write_to_file(old_file, s_data.encode("utf-8")) 
					#q_server=queqe_server()
					#q_server.lpush("TRANS", s_data.encode("utf-8"))
					#q_server.connection.disconnect()
				except Exception, e:
					#resp+=u"ERROR:%s\n"%e
					appendFile(request + e)
					errorLog(request)
					raise e
				else:
					device.OpLogStamp = request.REQUEST['OpStamp']
					resp += "OK:%d\n" % 1
			elif request.REQUEST.has_key('FPImage'):#/iclock/cdata?SN=7160048&FPImage=/tmp/enroll_fp_1.bmp&PIN=968&FID=0
				pin = request.REQUEST["PIN"]
				try:
					fid = request.REQUEST["FID"]
				except: fid = 0
				fName = request.REQUEST['FPImage']
				fName = os.path.split(fName)[1]
				fName = os.path.splitext(fName)
				fname = getUploadFileName("%s/fpimage/%s-%s" % (device.SN, pin, fid), fName[0].split("_")[-1], fName[1])
				try:
					os.makedirs(os.path.split(fname)[0])
				except:
					appendFile(request + e)
					errorLog(request)
				f = file(fname, "wb")
				d = request.raw_post_data
				f.write(d)
				f.close()
			else:
				resp += "UNKOWN DATA\n"
			print "---------------> ready to update stamp------------------------"
			checkAndSave(device)
			print "---------------> end to update stamp------------------------"
			resp += "POST from: " + device.SN + "\n"
	except  Exception, e:
		import traceback;traceback.print_exc()
		appendFile(request + e)
		errorLog(request)
		resp = "%s" % e
	response["Content-Length"] = len(resp)
	response.write(resp)	
	return response

	encrypt = 1
	response = HttpResponse(mimetype='text/plain')
	getResp = ""
	try:
		cursor = conn.cursor()
		resp = ""
		device = checkDevice(request, response)
		if(device == None): return response
		if request.REQUEST.has_key('action'):
			resp += "OK\n"
		elif request.method == 'GET':
			if request.REQUEST.has_key('PIN'):
				save = request.REQUEST.has_key('save') and (request.REQUEST['save'] in ['1', 'Y', 'y', 'yes', 'YES']) or False
				try:
					pin = request.REQUEST['PIN']
					emp = employee.objects.get(PIN=formatPIN(pin))
				except ObjectDoesNotExist:
					resp += "NONE"
					errorLog();
				else:
					cc = u"DATA UPDATE USERINFO PIN=%s\tName=%s\tPasswd=%s\tGrp=%d\tCard=%s\tTZ=%s\tPri=%s\n" % (emp.pin(), emp.EName or "", emp.Password or "", emp.AccGroup or 1, getNormalCard(emp.Card), emp.TimeZones or "", save and emp.Privilege or 0)
					for fp in fptemp.objects.filter(UserID=emp):
						try:
							#cc+=u"DATA FP PIN=%s\tFID=%d\tTMP=%s\n"%(emp.pin(), fp.FingerID, fp.temp())
							cc += u"DATA UPDATE FINGERTMP PIN=%s\tFID=%d\tSize=%s\tTMP=%s\n" % (emp.pin(), fp.FingerID, len(fp.temp()), fp.temp()) #by super 2010-07-24
						except:pass
					try:
						resp += cc.encode("gb18030")
					except:
						resp += cc.decode("utf-8").encode("gb18030")
					if not save: # if not saved user in device, delete it after serveral(5) minutes
						endTime = datetime.datetime.now() + datetime.timedelta(0, 5 * 60)
						appendDevCmd(device, "DATA DELETE USERINFO PIN=%s" % emp.pin(), cursor, endTime)
			else:
				resp += "GET OPTION FROM: %s\n" % device.SN
				#resp+="Stamp=%s\n"%device.LogStamp
				#resp+="OpStamp=%s\n"%(device.OpLogStamp or 0)
				#resp+="PhotoStamp=%s\n"%(device.PhotoStamp or 0)
				resp += "ATTLOGStamp=%s\n" % device.LogStamp
				resp += "OPERLOGStamp=%s\n" % (device.OpLogStamp or 0)
				resp += "ATTPHOTOStamp=%s\n" % (device.PhotoStamp or 0)					
				resp += "ErrorDelay=%d\n" % max(30, settings.MIN_REQ_DELAY)
				resp += "Delay=%d\n" % settings.MIN_REQ_DELAY
				resp += "TransTimes=%s\n" % device.TransTimes
				resp += "TransInterval=%d\n" % max(settings.MIN_TRANSINTERVAL, device.TransInterval)
				#resp+="TransFlag=%s\n"%device.UpdateDB
				resp += "TransFlag=%s\n" % device.UpdateDB.replace("\\t", "\t") #by super 2010-07-30
				resp += "ServerVer=0.0.2 2010-07-22\n"#by super 2010-07-22 新增
				try:
					if not (device.TZAdj == None):
						resp += "TimeZone=%s\n" % device.TZAdj
				except:
						appendFile("---------Error:Device is null!----------")
				resp += "Realtime=%s\n" % settings.TRANS_REALTIME
				resp += "Encrypt=%s\n\n" % settings.ENCRYPT
				encrypt = 0
			checkAndSave(device)
		elif request.method == 'POST':
#			file("c:/postdata.txt", "a+").write("POST FROM: %s(%s)%s----------\n"%(request.META["REMOTE_ADDR"],request.META["PATH_INFO"],datetime.datetime.now()))
#			file("c:/postdata_%s.txt"%(ccc), "a+").write(request.raw_post_data)
			raw_Data = request.raw_post_data
			if settings.ENCRYPT:
				import lzo
				rawData = lzo.bufferDecrypt(raw_Data, device.SN)
			else:
				rawData = raw_Data			
			if request.REQUEST.has_key('Stamp') and request.REQUEST.has_key('table'):
				if request.REQUEST['table'] == 'ATTLOG':#by super 2010-07-22 上传考勤记录
					okc = 0;
					errorLines = [] #发生保存错误的记录
					cacheLines = [] #本次提交的行
					errorLogs = []  #解析出错、不正确数据的行
					sqls = []
					commitLineCount = 700 #达到700行就提交一次
					if settings.DATABASE_ENGINE == "ado_mssql": commitLineCount = 50
					alog = None
					for line in string.split(rawData, "\n"):
						if line:
							eMsg = ""
							try:
								log = lineToLog(device, line)
							except Exception, e:  #行数据解析错误
								eMsg = u"%s" % e.message
								errorLog(request)
								log = None
							if log:
								sqls.append(log)
								cacheLines.append(line) #先记住还没有提交数据，commit不成功的话可以知道哪些数据没有提交成功
								if len(cacheLines) >= commitLineCount: #达到一定的行就提交一次
									try:
										commitLog(cursor, ';'.join(sqls))
										okc += len(cacheLines)
										if not alog:
											alog = cacheLines[0]
									except Exception, e:
										conn._rollback()
										conn.close()
										cursor = conn.cursor()
										errorLines += cacheLines
									cacheLines = []
									sqls = []
							else:
								errorLogs.append("%s\t--%s" % (line, eMsg and eMsg or "Invalid Data"))
					if cacheLines: #有还没有提交的数据
						try:
							commitLog(cursor, ';'.join(sqls))
							okc += len(cacheLines)
							if not alog:
								alog = cacheLines[0]
						except Exception, e:
							conn._rollback()
							conn.close()
							cursor = conn.cursor()
							errorLines += cacheLines
					if errorLines: #重新保存上面提交失败的数据，每条记录提交一次，最小化失败记录数
						cacheLines = errorLines
						errorLines = []
						for line in cacheLines:
							if line not in errorLogs:
								try:
									log = lineToLog(device, line)
									commitLog(cursor, log)
									if not alog: alog = cacheLines[0]
									okc += 1
								except Exception, e:
									estr = u"%s" % e.message
									if "database is locked" in estr:
										try:
											conn.close()
											cursor = conn.cursor()
											log = lineToLog(device, line)
											commitLog(cursor, log)
											if not alog: alog = cacheLines[0]
											okc += 1
										except Exception, ee:
											estr = u"%s" % ee.message
									elif ("UNIQUE KEY" in estr) or ("are not unique" in estr) or (
										"Duplicate entry" in estr):
											estr = "Duplicated"
									errorLines.append("%s\t--%s" % (line, estr))
					errorLines += errorLogs
					dlogObj = ""
					try:
						if okc == 1:
							dlogObj = alog
						elif okc > 1:
							dlogObj = alog + ", ..."
					except:pass
					log = devlog(SN=device, Cnt=okc, ECnt=len(errorLines), Object=dlogObj[:20], OpTime=datetime.datetime.now())
					log.save()
					if errorLines:
						tmpFile("transaction_%s_%s.txt" % (device.SN, log.id), "\n".join(errorLines))
					device.LogStamp = request.REQUEST['Stamp']
					saveDeviceStamp(cursor, device, 'logstamp', device.LogStamp)
					resp += "OK:%d\n" % okc
				elif request.REQUEST['table'] == 'OPERLOG':#by super 2010-07-22 上传用户信息和系统日志
					c = 0;
					ec = 0;
					try:
						user = False				
						for line in string.split(rawData, "\n"):
							try:
								if line:
									user = lineToUser(cursor, device, line)									
									c = c + 1
							except Exception, e:
								cursor = conn.cursor()
								ec = ec + 1
								errorLog(request)
								appendFile("ERROR(cdata-devview):%s\nLine:%s\nPost Data: %s\n-----Post Error---" % (e, line, rawData))
						try:
							conn._commit()
						except:
							errorLog(request)
						dlogObj = "TMP"
						try:
							dlogObj = (u"%s" % user)[:20]
						except: pass
						devlog(SN=device, Cnt=c, OP=u"USERDATA", ECnt=ec, Object=dlogObj, OpTime=datetime.datetime.now()).save()
						device.OpLogStamp = request.REQUEST['Stamp']
						saveDeviceStamp(cursor, device, 'oplogstamp', device.OpLogStamp)
						resp += "OK:%d\n" % c
					except Exception, e:
						resp += "ERROR: %s" % e
						errorLog(request)
				elif request.REQUEST['table'] == 'ATTPHOTO':#by super 2010-07-24 上传照片
					return postPhoto(request)			
			elif request.REQUEST.has_key('FPImage'):#/iclock/cdata?SN=7160048&FPImage=/tmp/enroll_fp_1.bmp&PIN=968&FID=0
				pin = request.REQUEST["PIN"]
				try:
					fid = request.REQUEST["FID"]
				except: fid = 0
				fName = request.REQUEST['FPImage']
				fName = os.path.split(fName)[1]
				fName = os.path.splitext(fName)
				fname = getUploadFileName("%s/fpimage/%s-%s" % (device.SN, pin, fid), fName[0].split("_")[-1], fName[1])
				try:
					os.makedirs(os.path.split(fname)[0])
				except:
					errorLog(request)
				f = file(fname, "wb")
				d = request.raw_post_data
				f.write(d)
				f.close()
			else:
				resp += "UNKOWN DATA\n"
			checkAndSave(device)
			resp += "POST from: " + device.SN + "\n"
	except  Exception, e:
		errorLog(request)
		resp = "%s" % e
	response["Content-Length"] = len(resp)
	response.write(resp)	
	return response

#设备读取命令

def getreq(request):
	#by super 2010-08-14 
	#设备第一次向服务器请求命令，或者设备新登记用户／指纹和有新的考勤记录时
	# GET http://host/iclock/getrequest?SN=xxxx&INFO=Ver 6.39 Apr 28 2008,2,0,0,192.168.1.201 
	#INFO=固件版本号,登记用户数,登记指纹数,考勤记录数,考勤机IP地址,指纹算法
	response = HttpResponse(mimetype='text/plain')
	try:
		resp = ""
		device = checkDevice(request, response)
		#add by Louis 2011-12-26,device status,
		device.state=DEV_STATUS_OK
		device.LastActivity=datetime.datetime.now()
		if(device == None): return response
		info = request.GET.get("INFO", "") #版本号，用户数,指纹数,记录数
		if info:
			info = info.split(",")
			if len(info) >= 6: #by super 2010-08-11 新增指纹算法标志
				info = "FWVersion=%s\nUserCount=%s\nFPCount=%s\nTransactionCount=%s\nIPAddress=%s\nFPVersion=%s\n" % tuple(info[:6])
			elif len(info) == 5: 
				info = "FWVersion=%s\nUserCount=%s\nFPCount=%s\nTransactionCount=%s\nIPAddress=%s\n" % tuple(info)
			elif len(info) == 4:
				info = "FWVersion=%s\nUserCount=%s\nFPCount=%s\nTransactionCount=%s\n" % tuple(info)
			else:
				info = ""
			if info: saveDevInfo(device, info)
		# 自动升级固件功能
		if up_version and settings.UPDATE_COUNT < settings.MAX_UPDATE_COUNT:
			fw = device.FWVersion
			if fw:
				fw = fwVerStd(fw)
				if fw < up_version and 0 == isUpdatingFW(device):
					if cache.get("upd_" + device.SN):
						errMsg = u"Firmware sent already"
					else:
						errMsg = devUpdateFirmware(device)
						if not errMsg: cache.set("upd_" + device.SN, "1", 30 * 60) #30分钟后再能再次发送升级命令
					if errMsg: #升级命令错
						appendFile((u"%s UPGRADE FW %s:%s" % (device.SN, fw, errMsg)))
						pass
					else:
						settings.UPDATE_COUNT += 1

		c = 0

		cmds = deviceCmd(device)
		if len(cmds) > 0:
			maxRet = 200 #最多传200条命令
			maxRetSize = 40 * 1024	#最多传40K数据
			for acmd in cmds:
				if acmd.CmdTransTime == None or ((datetime.datetime.now() - acmd.CmdTransTime).seconds >=0):
					#if acmd.CmdContent.find("DATA USER ")==0 or acmd.CmdContent.find("SMS ")>=0 or acmd.CmdContent.find("WEATHER ")==0: #传送用户命令,需要解码成GB2312
					if acmd.CmdContent.find("DATA USER ") == 0 or acmd.CmdContent.find("DATA UPDATE USERINFO") == 0 or acmd.CmdContent.find("SMS ") >= 0 or acmd.CmdContent.find("WEATHER ") == 0: #传送用户命令,需要解码成GB2312
						cc = acmd.CmdContent
						try:
							cc = cc.encode("gb18030")
						except:
							try:
								cc = cc.decode("utf-8").encode("gb18030")
							except:
								errorLog(request)
					else:
						cc = str(acmd.CmdContent)
					#if cc: resp+="C:%d:%s\n"%(acmd.id,cc)
					if cc: 
						#by super 2010-08-11 若指纹模板版本不对应，则不允许下载
						devObject = getDevice(acmd.SN_id)
						if devObject.FPVersion:
							#获取指纹模板
							if "TMP=" in cc:								
								tmp = cc.split("TMP=")
								if tmp and len(tmp) > 1:tmp = tmp[1]
								#判断指纹模板种类
								FPVersion1 = ""
								if tmp :
									if tmp[:3] == "oco":FPVersion1 = "9"
									else:FPVersion1 = "10"
								if FPVersion1 and FPVersion1 != devObject.FPVersion:cc = ""#若指纹模板算法与机器算法不一致，则不允许下载
						if cc:resp += "C:%d:%s\n" % (acmd.id, cc)
					if acmd.CmdTransTime:  #如果是上次已经传送过的命令，则本次少送10条，因为以前的传送可能是因为数据太大而传送失败
						maxRet -= 10
					acmd.CmdTransTime = datetime.datetime.now()
					acmd.save()
					c = c + 1;
					if (c > maxRet) or (len(resp) > maxRetSize): break;
					if acmd.CmdContent in ["REBOOT", "RESTART"]: break; #重新启动命令只能是最后一条指令
		if c == 0:
			resp += "OK"
		checkAndSave(device)
	except  Exception, e:
		resp = "%s" % e
		errorLog(request)
	if settings.ENCRYPT:
		import lzo
		resp = lzo.bufferEncrypt(resp + "\n", device.SN)
	response["Content-Length"] = len(resp)
	response.write(resp)
	return response

def getValueFrom(data, key):
	for l in data:
		if l.find(key + "=") == 0:
			return l[len(key) + 1:]
	return ""

def saveDevInfo(device, pdata):#用详细的字段代替INFO
	info_list = ['FWVersion', 'FPCount', 'TransactionCount', 'UserCount', 'MainTime', '~MaxFingerCount', '~LockFunOn',
					'~MaxAttLogCount', '~DeviceName', '~AlgVer', 'FlashSize', 'FreeFlashSize', 'Language',
					'VOLUME', 'DtFmt', 'IPAddress', 'IsTFT', '~Platform', 'Brightness', 'BackupDev', '~OEMVendor', 'FPVersion']
	pd = setValueDic(pdata)
	try:
		for k in pd.keys():
			if k == "~Platform" and "_TFT" in pd[k]: device.IsTFT = "1"
			if k in info_list:
				vlaues = 0
				if k[0] == '~':
					t = k[1:]
					if t == 'MaxAttLogCount':
						values = 10000 * string.atoi(pd[k])
					elif t == 'MaxFingerCount':
						values = 100 * string.atoi(pd[k])
					else:
						values = pd[k]
					k = t
				else:
					values = pd[k]
				device.__setattr__(k, values)
	except:
		errorLog(None)
	if ip4_re.search(device.Alias) and ip4_re.search(device.IPAddress):
		device.Alias = device.IPAddress
	device.save()

#def hostIsOld(request):
#	if request.META["HTTP_HOST"].find("10.25.14.51")==0: #"10.25.14.51"
#		return True
#	return False

def checkUpgradeFW(cmdobj, request):
	appendDevCmd(cmdobj.SN, "REBOOT")#add by liupan,2011-12-01
	if cmdobj.CmdContent.find("PutFile file/fw/") == 0 and cmdobj.CmdContent.find("main.gz") > 0: #upgrade firmware
		settings.UPDATE_COUNT -= 1
		url, fname = getFW(cmdobj.SN)
		diff = int(cmdobj.CmdReturn) - os.path.getsize(fname) #返回的文件字节数和实际的文件字节数比较
		if diff in [0, 1]: #升级成功, 有一旧版本的固件下载文件后会多出一个字节
#			if cmdobj.SN.OEMVendor !="ZKSoftware Inc.": appendDevCmd(cmdobj.SN, "SET OPTION ~OEMVendor ZKSoftware Inc.")
			if cmdobj.CmdContent.find("main.gz.tmp") > 0:
				appendDevCmd(cmdobj.SN, "Shell mv main.gz.tmp /mnt/mtdblock/main.gz")
#			if hostIsOld(request):
#				appendDevCmd(cmdobj.SN, "SET OPTION WebServerIP 10.33.109.27")
			appendDevCmd(cmdobj.SN, "REBOOT")
		else:
			appendDevCmd(cmdobj.SN, cmdobj.CmdContent) #重新升级

def checkUpLoadFile(request, data):
	d = request.raw_post_data
	index = d.find("Content=")
	if not index: return
	d = d[index + 8:]
	if not d: return
	try:
		fname = data['FILENAME']
	except:
		fname = ""
	if not fname: return
	fname = getUploadFileName(data["SN"], data['ID'], fname)
	try:
		os.makedirs(os.path.split(fname)[0])
	except:
		pass #errorLog(request)
	f = file(fname, "wb")
	f.write(d)
	f.close();

def responseStr(response, str):
	response["Content-Length"] = len(str)
	response.write(str)
	return response

def parseAPost(data, split):
	p = {}
	ditems = data.split(split)
	for i in range(len(ditems)):
		if not ditems[i]: continue
		k = ditems[i].split("=", 1)
		if k[0] == 'Content':
			p['Content'] = k[1] + split + split.join(ditems[i + 1:])
			break;
		elif ditems[i].find("CMD=INFO") >= 0:
			p['CMD'] = "INFO"
			p['Content'] = split.join(ditems[i + 1:])
			break;
		elif len(k) == 2:
			p[k[0]] = k[1]
	return p

def parsePosts(data):
	posts = []
	lines = data.split("\n")
	if len(lines) == 0: return posts
	firstline = lines[0].split("&")
	if len(firstline) < 2: #just a posts
		return [parseAPost(data, "\n")]
	if "CMD=INFO" in firstline:
		d = parseAPost(lines[0], "&")
		d['CMD'] = "INFO"
		d['Content'] = "\n".join(lines[1:])
		return [d]
	for l in lines:
		if l:
			posts.append(parseAPost(l, "&"))
	return posts

def updateCmd(id, ret):
	try:
		cmdobj = devcmds.objects.get(id=id)
	except ObjectDoesNotExist:
		return None
	cmdobj.CmdOverTime = datetime.datetime.now()
	cmdobj.CmdReturn = ret
	cmdobj.save()
	return cmdobj

def updateCmds(rets):
	for i in rets:
		updateCmd(i, rets[i])
#	cursor=conn.cursor()
#设备返回数据
def devpost(request):
	response = HttpResponse(mimetype='text/plain')
	resp = ""
	device = checkDevice(request, response)
	if(device == None): return response
	try:
		rd = request.raw_post_data
		if settings.ENCRYPT:
			try:
				import lzo
				rawData = lzo.bufferDecrypt(rd, device.SN)
			except:
				rawData = rd
		else:
			rawData = rd
		try:
			data0 = rawData.decode("gb18030")
		except:
			data0 = rawData
		rets = {}
		InfoLines = []
		pdata = parsePosts(data0)
#		appendFile("%s"%pdata)
		for apost in pdata:
			id = int(apost["ID"])
			ret = apost["Return"]
			if apost["CMD"] == "INFO":
				saveDevInfo(device, apost['Content'])
				rets[id] = ret
			elif apost['CMD'] == 'PutFile' and ret > 100 * 1024:
				cmdobj = updateCmd(id, ret)
				if cmdobj: checkUpgradeFW(cmdobj, request)
			elif (apost["CMD"] == "GetFile" or apost["CMD"] == "Shell") and ret > 0:
				checkUpLoadFile(request, apost)
				rets[id] = ret
			else:
				rets[id] = ret
		if len(rets) > 0:
			updateCmds(rets)
		resp += "OK"
		checkAndSave(device)
		try:
			nocmd_device = cache.get("nocmd_device")
			if nocmd_device:
				nocmd_device.remove(device.SN)
				cache.set("nocmd_device", nocmd_device)
		except: pass
	except  Exception, e:
		errorLog(request)
	response["Content-Length"] = len(resp)
	response.write(resp)
	return response

# Photo
#	/iclock/fdata?SN=88888888&PIN=20081119172119-4.jpg&PhotoStamp=285528079

def postPhoto(request):
	response = HttpResponse(mimetype='text/plain')
	device = checkDevice(request, response)
	if(device == None): return response
	try:
		pin = request.REQUEST["PIN"]
		pin = pin.split(".")[0].split("-")
		dt = pin[0]
		if len(pin) == 2: #Success Picture
			pin = pin[1]
		else:
			pin = None
		fname = getUploadFileName("%s/%s/%s" % (device.SN, dt[:4], dt[4:8]), pin, dt[8:] + ".jpg")
		try:
			os.makedirs(os.path.split(fname)[0])
		except:
			pass #errorLog(request)
		f = file(fname, "wb")
		d = request.raw_post_data
		if "CMD=uploadphoto" in d: d = d.split("CMD=uploadphoto")[1][1:]
		if "CMD=realupload" in d: d = d.split("CMD=realupload")[1][1:]
		f.write(d)
		f.close()
		#if request.REQUEST.has_key('PhotoStamp'):
		if request.REQUEST.has_key('Stamp') and request.REQUEST.has_key('table') and request.REQUEST['table'] == 'ATTPHOTO':#by super 2010-07-22 修改参数
			devlog(SN=device, Cnt=1, OP=u"PICTURE", Object=pin, OpTime=datetime.datetime.now()).save()
			#device.PhotoStamp=request.REQUEST['PhotoStamp']
			device.PhotoStamp = request.REQUEST['Stamp']
			saveDeviceStamp(conn.cursor(), device, 'photostamp', device.PhotoStamp)
			conn._commit()
			cache.set("iclock_" + device.SN, device)
	except  Exception, e:
		errorLog(request)
	response.write("OK\n")
	return response

#写入到队列，后台进程在进行实际的数据库写入操作

def write_to_file(file_name, s_data):
	i = 0
	while True:
		old_file = file_name + (i and "_%s.txt.tmp" % i or ".txt.tmp")
		try:
			f = file(old_file, "w+b")
		except:
			i += 1
		else:
			break;
	f.write(s_data)
	#f.flush()
	f.close()

	i = 0
	while True:
		try:
			if i == 0:
				os.rename(old_file, old_file[:-4])
			else:
				os.rename(old_file, old_file[:-8] + "_%s.txt" % i)
		except Exception, e:
			time.sleep(0.02)
			if i < 100:
				i += 1
			else:
				appendFile(u"ERROR:%s, filename=%s, %s\n" % (e, old_file, i))
				raise e
		else:
			break
	
def real_ip(request):
	alias = request.META.get('HTTP_X_FORWARDED_FOR', '')
	if not alias:
		alias = request.META.get('HTTP_X_REAL_IP', '')
	if not alias:
		alias = request.META.get('REMOTE_ADDR', '')
	return alias

def decode_data(raw):
	try:
		return raw.decode("gb18030")
	except:
		pass
	lines = []
	for line in raw.splitlines():
		try:
			lines.append(line.decode("gb18030"))
		except:
			pass
	return "\n".join(lines)


