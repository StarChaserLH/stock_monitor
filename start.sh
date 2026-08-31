#!/bin/bash
export PATH="/usr/local/bin:$PATH"

# 自动获取脚本所在目录，作为项目根目录
# 1. 获取脚本真实绝对路径
SCRIPT_PATH=$(realpath "$0")
# 2. 截取脚本所在文件夹
PROJECT_ROOT=$(dirname "$SCRIPT_PATH")

WWW_DATA_HOME="/var/www/.wwwdata-home"
LOCK_FILE="${PROJECT_ROOT}/.service.lock"

cleanup(){
	flock -u 9
	rm -f "${LOCK_FILE}"
	pkill -P $$ python
}
trap cleanup SIGINT SIGTERM EXIT
exec 9>"${LOCK_FILE}"
flock -n 9 || {
	echo "进程已在运行，禁止重复启动"
	exit 1
}
cd "${PROJECT_ROOT}" || exit 1
# 捕获程序退出码
env HOME="${WWW_DATA_HOME}" pipenv run python run.py
EXIT_CODE=$?
# 程序正常退出(0)时主动返回1，触发systemd重启
if [ ${EXIT_CODE} -eq 0 ];then
	exit 1
fi
exit ${EXIT_CODE}
